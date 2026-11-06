#!/usr/bin/env python3
"""Validate typed Pulse backend-tail evidence without radio or audio access."""

IDENTITY_FIELDS = ('session_id', 'build_id', 'source_commit')
SCOPE = 'Pulse-reported backend tail; physical RF-off timing unmeasured'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def integer(record, field, minimum=0):
    value = record.get(field)
    require(type(value) is int and minimum <= value <= (1 << 64) - 1,
            'Output contract has an invalid integer: ' + field)
    return value


def identity(record, expected):
    for field in IDENTITY_FIELDS:
        value = record.get(field)
        require(isinstance(value, str) and 0 < len(value) <= 200,
                'Output contract has an invalid identity: ' + field)
        require(value == expected.get(field), 'Output contract identity mismatch: ' + field)


def validate_output_contract(rows, completion, required_version=1, expected_identity=None):
    """Return PASS only for complete, session-bound v1 backend-tail evidence.

    Version zero is an explicit historical diagnostic mode. Interrupted receipts
    preserve raw fault values and never establish a completed playback barrier.
    """
    require(type(required_version) is int and required_version in (0, 1),
            'Unsupported required USB output contract version.')
    summary = dict(schema='x6200.usb.output-contract.v1', status='DIAGNOSTIC_ONLY',
                   usb_output_contract_version=0, completed_turns=0, completed_intervals=0,
                   interrupted_intervals=0, receipt_count=0, scope=SCOPE)
    if required_version == 0:
        return summary
    require(isinstance(rows, (list, tuple)) and all(isinstance(row, dict) for row in rows),
            'Output contract trace must contain JSON objects.')
    sessions = [row for row in rows if row.get('kind') == 'session']
    require(len(sessions) == 1 and sessions[0].get('schema') == 'x6200.usb-audio.v3',
            'USB output contract requires exactly one v3 session header.')
    session = sessions[0]
    require(isinstance(completion, dict), 'USB output contract requires an immutable completion record.')
    for record in (session, completion):
        require(type(record.get('usb_output_contract_version')) is int and
                record['usb_output_contract_version'] == 1,
                'USB output contract requires version 1 in the session header and completion.')
    expected = session if expected_identity is None else expected_identity
    require(isinstance(expected, dict), 'Output contract expected identity must be an object.')
    identity(session, expected)
    identity(completion, expected)
    require(completion.get('kind') == 'completion' and
            completion.get('schema') == 'x6200.usb-completion.v1' and
            completion.get('terminal') is True and completion.get('trace_complete') is True,
            'Output contract requires a complete immutable terminal record.')
    turns = integer(completion, 'turns')
    completed_count = integer(completion, 'playback_tail_completed_intervals')
    interrupted_count = integer(completion, 'playback_tail_interrupted_intervals')
    state, fault = integer(completion, 'state'), integer(completion, 'fault')
    require((state == 5 and fault > 1) or (state == 6 and fault == 1),
            'Output contract requires terminal Stop/fault chronology.')
    samples = [row for row in rows if row.get('kind') == 'sample']
    require(samples, 'Output contract has no final state sample.')
    final = samples[-1]
    require(integer(final, 'state') == state and integer(final, 'fault') == fault,
            'Output contract terminal state/fault conflicts with the final snapshot.')
    require(integer(final, 'turns') == turns,
            'Output contract completed turn count conflicts with the final snapshot.')
    require(integer(final, 'ms') == integer(completion, 'last_sample_ms'),
            'Output contract completion does not identify the final snapshot.')
    qualification_turns = integer(completion, 'qualification_turns')
    require(qualification_turns <= turns,
            'Output contract qualification count exceeds completed turns.')

    # State events are emitted by the same controller as the completed counter.
    # Qualification uses these transitions too, without physical RF ON intervals.
    intervals, current = [], None
    for row in rows:
        if row.get('kind') != 'event' or row.get('event') != 'state':
            continue
        state = row.get('state')
        if state == 3:
            require(current is None, 'Output contract starts TX before the previous turn ended.')
            current = dict(start_ms=integer(row, 'ms'))
        elif state == 4 and current is not None:
            require('drain_ms' not in current, 'Output contract repeats a drain boundary.')
            current['drain_ms'] = integer(row, 'ms')
        elif state == 8 and current is not None:
            require('drain_ms' in current, 'Output contract completed turn omitted drain.')
            current['unkey_ms'] = integer(row, 'ms')
            intervals.append(current)
            current = None
    require(len(intervals) == turns,
            'Output contract completed turns do not match normal TX/drain/unkey intervals.')
    receipts = [row for row in rows if row.get('kind') == 'backend-tail' or
                str(row.get('schema', '')).startswith('x6200.usb.playback-tail.')]
    require(len(receipts) == completed_count + interrupted_count,
            'Output contract receipt count conflicts with immutable completion.')
    require(sum(row.get('complete') is True for row in receipts) == completed_count and
            sum(row.get('complete') is False for row in receipts) == interrupted_count,
            'Output contract interval counters conflict with receipt outcomes.')
    seen = set()
    for receipt in receipts:
        require(receipt.get('kind') == 'backend-tail' and
                receipt.get('schema') == 'x6200.usb.playback-tail.v1',
                'Output contract requires a typed playback-tail.v1 receipt.')
        identity(receipt, expected)
        ordinal = integer(receipt, 'turn', 1)
        require(ordinal not in seen and ordinal <= turns + 1,
                'Output contract has duplicate or unrelated turn receipts.')
        seen.add(ordinal)
        for field in ('complete', 'rf_tx_confirmed', 'rf_disabled'):
            require(type(receipt.get(field)) is bool,
                    'Output contract receipt has an invalid boolean: ' + field)
        require(receipt.get('scope') == 'pulse-reported-backend-tail',
                'Output contract receipt has an unsupported timing scope.')
        if ordinal <= turns:
            require(receipt['complete'] is True,
                    'Output contract completed turn has no completed backend barrier.')
            interval = intervals[ordinal - 1]
            if ordinal <= qualification_turns:
                require(receipt['rf_disabled'] is True,
                        'Output contract completed qualification turn was not RF disabled.')
        else:
            require(current is not None and 'drain_ms' in current,
                    'Output contract extra interval has no interrupted TX/drain chronology.')
            interval = current
        if not receipt['complete']:
            require(ordinal == turns + 1 and receipt['rf_tx_confirmed'] is False,
                    'Output contract interrupted receipt cannot qualify a completed turn.')
            continue

        require(receipt['rf_tx_confirmed'] is True,
                'Output contract completed backend interval lacks TX confirmation.')
        fields = ('drain_started_ms', 'protocol_ack_ms', 'timing_issued_ms', 'timing_issued_us',
                  'timing_ack_ms', 'timing_ack_us', 'write_index_bytes', 'read_index_bytes',
                  'sink_usec', 'transport_usec', 'configured_sink_usec', 'protocol_pending_usec',
                  'wait_usec', 'wait_until_us', 'completed_us')
        values = {field:integer(receipt, field) for field in fields}
        require(values['write_index_bytes'] <= (1 << 63) - 1 and
                values['read_index_bytes'] <= (1 << 63) - 1 and
                values['write_index_bytes'] % 2 == values['read_index_bytes'] % 2 == 0,
                'Output contract playback indices are not native PCM16 frame boundaries.')
        pending_frames = max(values['write_index_bytes'] - values['read_index_bytes'], 0) // 2
        require(pending_frames <= 44100 * 3 and
                values['protocol_pending_usec'] == (pending_frames * 1000000 + 44099) // 44100,
                'Output contract unread playback budget is invalid.')
        require(values['wait_usec'] == values['sink_usec'] + values['transport_usec'] +
                values['protocol_pending_usec'], 'Output contract backend wait arithmetic is invalid.')
        require(values['timing_issued_ms'] == values['timing_issued_us'] // 1000 and
                values['timing_ack_ms'] == values['timing_ack_us'] // 1000,
                'Output contract timing units disagree.')
        require(interval['start_ms'] <= values['drain_started_ms'] <= interval['drain_ms'] <=
                values['protocol_ack_ms'] <= values['timing_issued_ms'] <= values['timing_ack_ms'] and
                values['timing_issued_us'] <= values['timing_ack_us'],
                'Output contract timing ACK is not fresh after protocol drain.')
        require(values['timing_ack_ms'] - values['timing_issued_ms'] < 1000,
                'Output contract timing ACK exceeded its one-second deadline.')
        require(values['wait_until_us'] == values['timing_ack_us'] + values['wait_usec'] + 1 and
                values['completed_us'] >= values['wait_until_us'],
                'Output contract backend interval closed before its monotonic tail wait.')
        deadline = (values['drain_started_ms'] + 3000) * 1000
        require(values['wait_until_us'] <= deadline and values['completed_us'] // 1000 <=
                values['drain_started_ms'] + 3000,
                'Output contract backend interval exceeded the unchanged drain deadline.')
        require(values['completed_us'] // 1000 <= final['ms'],
                'Output contract backend interval closed after the final snapshot.')
        if ordinal <= turns:
            require(values['completed_us'] // 1000 <= interval['unkey_ms'],
                    'Output contract normal unkey preceded the completed backend interval.')
        else:
            require(all(integer(row, 'ms') >= values['completed_us'] // 1000 for row in rows
                        if row.get('kind') == 'event' and row.get('event') == 'first-fault'),
                    'Output contract extra completed interval followed an earlier fault.')
    require(set(range(1, turns + 1)) <= seen,
            'Output contract is missing a backend-tail receipt for a completed turn.')
    summary.update(status='PASS', usb_output_contract_version=1, completed_turns=turns,
                   completed_intervals=completed_count, interrupted_intervals=interrupted_count,
                   receipt_count=len(receipts))
    return summary
