#!/usr/bin/env python3
"""Validate finite Pulse capture admission without radio or audio access."""

IDENTITY_FIELDS = ('session_id', 'build_id', 'source_commit')
SCOPE = 'Pulse capture production frontier; physical microphone/PTT latency unmeasured'
UINT64_MAX = (1 << 64) - 1
INT64_MAX = (1 << 63) - 1
RATE = 48000
DEADLINE_MS = 750


def require(condition, reason):
    if not condition:
        raise ValueError('Capture-open contract: ' + reason)


def integer(record, field, minimum=0, maximum=UINT64_MAX):
    value = record.get(field)
    require(type(value) is int and minimum <= value <= maximum,
            'invalid integer ' + field)
    return value


def identity(record, expected):
    for field in IDENTITY_FIELDS:
        value = record.get(field)
        require(isinstance(value, str) and 0 < len(value) <= 200,
                'invalid identity ' + field)
        require(value == expected.get(field), 'identity mismatch ' + field)


def _frontier(record):
    write = integer(record, 'write_index_bytes', 0, INT64_MAX)
    require(write % 2 == 0, 'write index is not a native PCM16 frame boundary')
    source = integer(record, 'source_usec')
    transport = integer(record, 'transport_usec')
    integer(record, 'sink_usec')  # Retained raw; never used as a correction.
    require(source + transport <= DEADLINE_MS * 1000,
            'raw source timing exceeds the unchanged 750ms horizon')
    extra = ((source + transport) * RATE + 999999) // 1000000
    require(integer(record, 'frontier_extra_frames') == extra,
            'frontier extra frames differ from raw source and transport timing')
    return write // 2 + extra


def _opening(record, expected):
    require(record.get('kind') == 'capture-open' and
            record.get('schema') == 'x6200.usb.capture-open.v1',
            'requires a typed capture-open.v1 receipt')
    identity(record, expected)
    integer(record, 'turn', 1)
    integer(record, 'generation', 1)
    for field in ('complete', 'canceled', 'index_gap', 'read_index_corrupt', 'write_index_corrupt'):
        require(type(record.get(field)) is bool, 'invalid boolean ' + field)
    require(integer(record, 'open_timeout_ms') == DEADLINE_MS and
            record.get('scope') == 'pulse-capture-production-frontier',
            'opening changed its deadline or timing scope')
    fields = ('begin_requested_ms', 'timing_issued_ms', 'frozen_ms', 'reached_ms',
              'opened_ms', 'capture_started_ms', 'begin_read_index', 'frontier_extra_frames',
              'start_index', 'discarded_prefix_frames', 'first_submitted_index',
              'source_usec', 'transport_usec', 'sink_usec')
    values = {field: integer(record, field) for field in fields}
    for field in ('read_index_bytes', 'write_index_bytes'):
        integer(record, field, -(1 << 63), INT64_MAX)
    if not record['complete']:
        # Incomplete records retain the actual invalid/cancelled raw values.
        # They cannot supply an admission boundary or qualify a TX turn.
        require(values['opened_ms'] == 0, 'interrupted opening claims committed admission')
        return values
    require(record['canceled'] is False, 'complete opening cannot also be canceled')
    require(record['read_index_corrupt'] is False and record['write_index_corrupt'] is False,
            'complete opening contains corrupt raw timing indices')
    read = integer(record, 'read_index_bytes', 0, INT64_MAX)
    require(read % 2 == 0, 'read index is not a native PCM16 frame boundary')
    start = _frontier(record)
    require(values['start_index'] == start and values['begin_read_index'] <= read // 2 <= start,
            'start index differs from its raw capture production frontier')
    require(start - read // 2 <= RATE * DEADLINE_MS // 1000,
            'opening frontier exceeds its bounded unread production horizon')
    require(values['discarded_prefix_frames'] == start - values['begin_read_index'] and
            values['first_submitted_index'] == start and record['index_gap'] is False,
            'opening admitted a prefix, gap or shifted first sample')
    times = [values[field] for field in ('begin_requested_ms', 'timing_issued_ms',
                                       'frozen_ms', 'reached_ms', 'opened_ms')]
    require(times == sorted(times) and values['capture_started_ms'] == values['frozen_ms'],
            'opening chronology or fixed source epoch is invalid')
    require(values['frozen_ms'] - values['timing_issued_ms'] < DEADLINE_MS and
            values['frozen_ms'] - values['begin_requested_ms'] < DEADLINE_MS and
            values['reached_ms'] - values['begin_requested_ms'] <= DEADLINE_MS,
            'opening exceeded the unchanged 750ms deadline')
    return values


def _closure(close, opening, open_values, accounting, fragment_frames):
    for field in ('complete', 'index_gap'):
        require(type(close.get(field)) is bool, 'invalid close boolean ' + field)
    require(close['complete'] is True and close['index_gap'] is False,
            'completed capture interval has no complete gap-free closure')
    require(close.get('scope') == 'pulse-capture-pipeline-frontier' and
            integer(close, 'close_timeout_ms') == DEADLINE_MS,
            'closure changed its timing scope or deadline')
    start = integer(close, 'start_index')
    cutoff = _frontier(close)
    require(start == open_values['start_index'] and
            integer(close, 'capture_started_ms') == open_values['capture_started_ms'],
            'closure changed the opening start index or source epoch')
    require(integer(close, 'cutoff_index') == cutoff and start <= cutoff and
            integer(close, 'submitted_first_index') == start and
            integer(close, 'submitted_end_index') == cutoff and
            integer(close, 'consumed_index') == cutoff,
            'closure does not conserve its exact admitted source interval')
    accepted = cutoff - start
    if 'mic_tap_start' in close or 'mic_tap_end' in close:
        tap_start, tap_end = integer(close, 'mic_tap_start'), integer(close, 'mic_tap_end')
        require(tap_end >= tap_start and tap_end - tap_start <= accepted,
                'bounded PCM tap exceeds the exact source interval')
    fence = integer(close, 'release_fence_index')
    require(start <= fence <= cutoff and integer(close, 'tail_frames') == cutoff - fence,
            'closure tail differs from the retained release fence')
    fields = ('close_started_ms', 'timing_issued_ms', 'frozen_ms', 'reached_ms', 'closed_ms')
    times = [integer(close, field) for field in fields]
    require(times == sorted(times) and open_values['opened_ms'] <= times[0] and
            times[2] - times[1] < DEADLINE_MS and times[-1] - times[0] <= DEADLINE_MS,
            'capture closure is unordered or exceeds the unchanged 750ms deadline')
    active_ms = times[0] - open_values['capture_started_ms']
    expected_frames = active_ms * RATE // 1000
    boundary = fragment_frames * 2
    lower = max(expected_frames * 99 // 100 - boundary, 0)
    upper = (expected_frames * 101 + 99) // 100 + boundary
    require(integer(accounting, 'active_ms') == active_ms and
            integer(accounting, 'accepted_mic_frames') == accepted and
            integer(accounting, 'expected_mic_frames') == expected_frames and
            integer(accounting, 'required_mic_frames') == lower and
            integer(accounting, 'boundary_frames') == boundary and
            integer(accounting, 'delivery_tolerance_percent') == 1,
            'source accounting changed its epoch, frames or existing tolerance')
    require(type(accounting.get('evaluated')) is bool and accounting['evaluated'] is (active_ms >= 750) and
            accounting.get('passed') is True and integer(accounting, 'ms') >= times[-1],
            'source accounting did not follow successful finite closure')
    if active_ms >= 750:
        require(accepted >= lower and fence - start <= upper,
                'accepted source frames violate the unchanged one-percent/two-fragment bounds')
    return dict(turn=opening['turn'], generation=opening['generation'], start_index=start, cutoff_index=cutoff,
                accepted_frames=accepted, capture_started_ms=open_values['capture_started_ms'],
                opened_ms=open_values['opened_ms'], closed_ms=times[-1])


def validate_capture_open_contract(rows, completion, required_version=1, expected_identity=None):
    """Return PASS only for complete, identity-bound finite capture evidence.

    Version zero explicitly labels historical traces diagnostic-only. Failed
    opening receipts remain useful evidence but never qualify an admitted turn.
    """
    require(type(required_version) is int and required_version in (0, 1),
            'unsupported required version')
    summary = dict(schema='x6200.usb.capture-open-contract.v1', status='DIAGNOSTIC_ONLY',
                   usb_capture_open_contract_version=0, completed_turns=0,
                   completed_openings=0, interrupted_openings=0,
                   receipt_count=0, completed_intervals=0, intervals=[], scope=SCOPE)
    if required_version == 0:
        return summary
    require(isinstance(rows, (list, tuple)) and all(isinstance(row, dict) for row in rows),
            'trace must contain JSON objects')
    sessions = [row for row in rows if row.get('kind') == 'session']
    require(len(sessions) == 1 and sessions[0].get('schema') in
            ('x6200.usb-audio.v3', 'x6200.usb-audio.v4'), 'requires one supported session header')
    session = sessions[0]
    require(isinstance(completion, dict), 'requires immutable completion')
    for record in (session, completion):
        require(type(record.get('usb_capture_open_contract_version')) is int and
                record['usb_capture_open_contract_version'] == 1,
                'requires version 1 in session and completion')
    expected = session if expected_identity is None else expected_identity
    require(isinstance(expected, dict), 'expected identity must be an object')
    identity(session, expected)
    identity(completion, expected)
    require(completion.get('kind') == 'completion' and
            completion.get('schema') == 'x6200.usb-completion.v1' and
            completion.get('terminal') is True and completion.get('trace_complete') is True,
            'requires a complete immutable terminal record')
    turns = integer(completion, 'turns')
    completed_count = integer(completion, 'capture_open_completed_intervals')
    interrupted_count = integer(completion, 'capture_open_interrupted_intervals')
    samples = [row for row in rows if row.get('kind') == 'sample']
    require(samples, 'missing final snapshot')
    final = samples[-1]
    state, fault = integer(completion, 'state'), integer(completion, 'fault')
    require((state == 5 and fault > 1) or (state == 6 and
            (fault == 1 or (fault == 0 and session.get('operational') is True and
                           completion.get('completion_reason') == 'navigation'))),
            'terminal state is neither Stop, fault nor graceful navigation')
    require(integer(final, 'state') == state and integer(final, 'fault') == fault and
            integer(final, 'turns') == turns and
            integer(final, 'ms') == integer(completion, 'last_sample_ms'),
            'completion conflicts with final snapshot')
    openings, attempts, closes, accounting = {}, [], {}, {}
    tx_attempts, completed_tx, current = [], [], None
    for position, row in enumerate(rows):
        if row.get('kind') != 'event' or row.get('event') != 'state':
            continue
        event_state = row.get('state')
        if event_state == 3:
            require(current is None, 'TX began before the previous interval ended')
            current = dict(start_ms=integer(row, 'ms'), position=position)
            tx_attempts.append(current)
        elif event_state == 4 and current is not None:
            require('drain_ms' not in current, 'repeated capture drain boundary')
            current['drain_ms'] = integer(row, 'ms')
        elif event_state == 8 and current is not None:
            end = integer(row, 'ms')
            require('drain_ms' in current and current['start_ms'] <= current['drain_ms'] <= end,
                    'unkey omitted or preceded its ordered capture drain')
            current['unkey_ms'] = end
            completed_tx.append(current)
            current = None
    require(len(completed_tx) == turns, 'completed turns disagree with TX/drain/unkey chronology')
    for position, row in enumerate(rows):
        kind = row.get('kind')
        if kind == 'capture-open' or str(row.get('schema', '')).startswith('x6200.usb.capture-open.'):
            values = _opening(row, expected)
            number, generation = integer(row, 'turn', 1), integer(row, 'generation', 1)
            key = (number, generation)
            require(number <= turns + 1 and key not in openings,
                    'duplicate or unrelated opening generation')
            if attempts:
                previous = attempts[-1]
                require(number >= previous[1]['turn'] and generation > previous[1]['generation'] and
                        values['begin_requested_ms'] >= previous[2]['begin_requested_ms'],
                        'opening attempts are not ordered')
            value = (position, row, values)
            openings[key] = value
            attempts.append(value)
        elif kind == 'capture-close':
            number, generation = integer(row, 'turn', 1), integer(row, 'generation', 1)
            key = (number, generation)
            require(key not in closes, 'duplicate capture-close receipt')
            for field in ('complete', 'index_gap'):
                require(type(row.get(field)) is bool, 'invalid close boolean ' + field)
            require(row.get('scope') == 'pulse-capture-pipeline-frontier' and
                    integer(row, 'close_timeout_ms') == DEADLINE_MS,
                    'closure changed its timing scope or deadline')
            closes[key] = (position, row)
        elif kind == 'source-accounting':
            number = integer(row, 'turn', 1)
            require(number not in accounting, 'duplicate source-accounting receipt')
            accounting[number] = (position, row)
    require(len(attempts) == completed_count + interrupted_count and
            sum(row['complete'] is True for _, row, _ in attempts) == completed_count and
            sum(row['complete'] is False for _, row, _ in attempts) == interrupted_count,
            'opening outcomes disagree with immutable completion counters')
    attempt_turns = {row['turn'] for _, row, _ in attempts}
    require(attempt_turns == set(range(1, max(attempt_turns, default=0) + 1)),
            'opening turns are not contiguous')
    successful = [value for value in attempts if value[1]['complete']]
    require(len(tx_attempts) == len(successful),
            'TX attempts do not match completed opening generations')
    chronology_by_key = {}
    completed_ordinal = 0
    for (position, opening, values), chronology in zip(successful, tx_attempts):
        key = (opening['turn'], opening['generation'])
        require(opening['turn'] == completed_ordinal + 1,
                'opening turn advanced without completed RF chronology')
        require(chronology['start_ms'] >= values['opened_ms'] and position < chronology['position'],
                'TX precedes completed opening')
        require(values['opened_ms'] <= final['ms'], 'opening follows final snapshot')
        chronology_by_key[key] = chronology
        if 'drain_ms' in chronology and 'unkey_ms' in chronology:
            completed_ordinal += 1
    descriptor = [row for row in rows if row.get('kind') == 'stream' and row.get('stream') == 'mic']
    if any(row['complete'] for _, row in closes.values()):
        require(len(descriptor) == 1 and integer(descriptor[0], 'rate') == RATE and
                integer(descriptor[0], 'channels') == 1, 'requires one native microphone descriptor')
        fragsize = integer(descriptor[0], 'fragsize', 2, (1 << 32) - 2)
        require(fragsize % 2 == 0, 'microphone fragment is not PCM16 aligned')
        fragment_frames = fragsize // 2
    else:
        fragment_frames = 0
    intervals, covered_accounting = [], set()
    closed_turns = set()
    for key, (close_position, close) in closes.items():
        require(key in openings, 'close has no same-generation opening receipt')
        position, opening, values = openings[key]
        require(position < close_position, 'closure precedes its opening receipt')
        if not close['complete']:
            continue
        require(opening['complete'] and key in chronology_by_key,
                'complete closure has no completed opening admission')
        number = opening['turn']
        require(number not in closed_turns, 'duplicate completed capture interval for a turn')
        closed_turns.add(number)
        require(number in accounting, 'complete closure omitted source accounting')
        account_position, account = accounting[number]
        require(position < close_position < account_position,
                'opening, closure and source accounting are not ordered')
        covered_accounting.add(number)
        interval = _closure(close, opening, values, account, fragment_frames)
        require(interval['closed_ms'] <= final['ms'], 'closure follows final snapshot')
        chronology = chronology_by_key[key]
        require('drain_ms' in chronology and
                close['capture_started_ms'] <= chronology['start_ms'] <= close['close_started_ms'] <=
                chronology['drain_ms'] <= close['timing_issued_ms'],
                'capture close is detached from its TX/drain chronology')
        if 'unkey_ms' in chronology:
            require(interval['closed_ms'] <= chronology['unkey_ms'],
                    'normal unkey preceded finite capture closure')
        intervals.append(interval)
    for key, chronology in chronology_by_key.items():
        if 'drain_ms' in chronology and 'unkey_ms' in chronology:
            require(key in closes and closes[key][1]['complete'],
                    'completed RF turn lacks its conserved capture interval')
    require(set(accounting) == covered_accounting,
            'source accounting has no complete same-generation capture interval')
    require(set(range(1, turns + 1)) <= closed_turns,
            'completed RF turn lacks its conserved capture interval')
    summary.update(status='PASS', usb_capture_open_contract_version=1, completed_turns=turns,
                   completed_openings=completed_count, interrupted_openings=interrupted_count,
                   receipt_count=len(attempts), completed_intervals=len(intervals), intervals=intervals)
    return summary
