#!/usr/bin/env python3
"""Observe qualification resources and hint only explicit task-owned clean cache.

DONTNEED is advisory: bytes advised are not bytes reclaimed. The helper opens
regular files read-only and never deletes files, writes their contents, calls
fsync on them, or uses a global drop_caches interface. The kernel may discard
clean reclaimable pages; dirty/mapped/tmpfs pages need not be reclaimed. Actual
cgroup headroom and pressure counters determine whether audio may start.
"""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import signal
import stat
import time

SCHEMA = 'x6200.usb.host-resources.v1'
GIB = 1024 ** 3
MIN_START_HEADROOM_BYTES = 4 * GIB
MIN_END_HEADROOM_BYTES = GIB
PRESSURE_COUNTERS = ('memory.events.high', 'memory.events.max', 'memory.events.oom',
                     'memory.events.oom_kill', 'memory.stat.pgscan_direct', 'memory.stat.pgsteal_direct')
FORBIDDEN_ROOTS = {Path(p) for p in ('/', '/workspace', '/tmp', '/home', '/var', '/usr', '/etc', '/proc', '/sys', '/dev')}
CPU_SCHEDULING_SCHEMA = 'x6200.usb.host-cpu-scheduling.v1'
NUMERICAL_THREAD_ENVIRONMENT = {name: '1' for name in (
    'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}


def cpu_scheduling_policy(affinity, quota_us, period_us):
    """Cap this test tree at three quarters of observed CPU capacity, min one.

    This limits this private test process tree, not the radio or the cgroup.
    It cannot prevent unrelated workloads or promise a scheduler deadline.
    """
    if not isinstance(affinity, (list, tuple)):
        raise ValueError('CPU scheduling affinity is not a list')
    affinity = list(affinity)
    if (not affinity or any(type(cpu) is not int or cpu < 0 for cpu in affinity) or
            affinity != sorted(set(affinity)) or type(period_us) is not int or period_us <= 0 or
            (quota_us is not None and (type(quota_us) is not int or quota_us <= 0))):
        raise ValueError('CPU scheduling requires a known quota/period and nonempty sorted affinity')
    count = max(1, min(3 * len(affinity) // 4,
                       3 * quota_us // (4 * period_us) if quota_us is not None else len(affinity)))
    return {'schema': CPU_SCHEDULING_SCHEMA, 'method': 'inherited_affinity_three_quarters_cpu_quota',
            'quota_us': quota_us, 'period_us': period_us, 'inherited_affinity': affinity,
            'selected_affinity': affinity[:count], 'thread_environment': dict(NUMERICAL_THREAD_ENVIRONMENT)}


def cpu_scheduling_configuration(cgroup_root=None):
    if not hasattr(os, 'sched_getaffinity') or not hasattr(os, 'sched_setaffinity'):
        raise ValueError('Linux CPU affinity control is required for the private audio campaign')
    quota, period = (_cgroup_directory(cgroup_root) / 'cpu.max').read_text().split()
    return cpu_scheduling_policy(sorted(os.sched_getaffinity(0)),
                                 None if quota == 'max' else _integer(quota), _integer(period))


def validate_cpu_scheduling_configuration(configuration):
    if (not isinstance(configuration, dict) or type(configuration.get('selected_affinity')) is not list or
            any(type(cpu) is not int for cpu in configuration['selected_affinity']) or configuration != cpu_scheduling_policy(
            configuration.get('inherited_affinity', []), configuration.get('quota_us'), configuration.get('period_us'))):
        raise ValueError('Host CPU scheduling policy differs from the fixed qualification policy')
    return configuration


def observe_cpu_scheduling(configuration):
    validate_cpu_scheduling_configuration(configuration)
    effective = sorted(os.sched_getaffinity(0))
    environment = {name: os.environ.get(name) for name in NUMERICAL_THREAD_ENVIRONMENT}
    reasons = []
    if effective != configuration['selected_affinity']:
        reasons.append('Effective CPU affinity differs from the declared test process-tree limit')
    if environment != configuration['thread_environment']:
        reasons.append('Numerical library thread environment differs from the declared single-thread limits')
    return {'schema': CPU_SCHEDULING_SCHEMA, 'status': 'BLOCKED' if reasons else 'PASS',
            'effective_affinity': effective, 'thread_environment': environment, 'reasons': reasons}


def apply_cpu_scheduling(configuration):
    """Apply once before loading codec/analysis libraries or starting children."""
    validate_cpu_scheduling_configuration(configuration)
    if sorted(os.sched_getaffinity(0)) != configuration['inherited_affinity']:
        raise ValueError('Inherited CPU affinity changed before the campaign scheduling policy was applied')
    os.environ.update(configuration['thread_environment'])
    os.sched_setaffinity(0, configuration['selected_affinity'])
    receipt = observe_cpu_scheduling(configuration)
    if receipt['status'] != 'PASS':
        raise ValueError('; '.join(receipt['reasons']))
    return receipt


def _save(path, value):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def _integer(text):
    value = int(text.strip())
    if value < 0:
        raise ValueError('negative resource value')
    return value


def _counter_file(path):
    result = {}
    for line in path.read_text().splitlines():
        name, value = line.split()
        if name in result:
            raise ValueError('duplicated resource counter: ' + name)
        result[name] = _integer(value)
    return result


def _cgroup_directory(explicit=None):
    if explicit is not None:
        return Path(explicit).resolve()
    base = Path('/sys/fs/cgroup')
    membership = next((line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines()
                       if line.startswith('0::')), None)
    if membership is None:
        raise ValueError('No cgroup v2 membership is available')
    candidate = base / membership.lstrip('/')
    if (candidate / 'memory.current').is_file():
        return candidate
    if membership == '/' and (base / 'memory.current').is_file():
        return base
    raise ValueError('Current cgroup v2 memory files are not accessible')


def resource_snapshot(cgroup_root=None):
    result = {'status': 'UNKNOWN', 'observed_monotonic_ns': time.monotonic_ns(),
              'errors': [], 'memory': {}, 'cpu': {}, 'headroom_bytes': None}
    try:
        group = _cgroup_directory(cgroup_root)
        result['cgroup_path'] = str(group)
        current = _integer((group / 'memory.current').read_text())
        limits = {}
        for name in ('max', 'high'):
            value = (group / ('memory.' + name)).read_text().strip()
            limits[name] = None if value == 'max' else _integer(value)
        events = _counter_file(group / 'memory.events')
        counters = _counter_file(group / 'memory.stat')
        for key in ('high', 'max', 'oom', 'oom_kill'):
            if key not in events:
                raise ValueError('Missing memory.events counter: ' + key)
        for key in ('anon', 'file', 'shmem', 'pgscan_direct', 'pgsteal_direct'):
            if key not in counters:
                raise ValueError('Missing memory.stat counter: ' + key)
        effective = min(value for value in limits.values() if value is not None) if limits['max'] is not None else None
        headroom = max(0, effective - current) if effective is not None else None
        result['memory'] = {'current_bytes': current, 'max_bytes': limits['max'], 'high_bytes': limits['high'],
                            'effective_limit_bytes': effective, 'headroom_bytes': headroom,
                            'events': events, 'stat': counters,
                            'clean_file_cache_estimate_bytes': max(0, counters['file'] - counters['shmem'])}
        quota, period = (group / 'cpu.max').read_text().split()
        period = _integer(period)
        if period == 0:
            raise ValueError('CPU quota period is zero')
        quota = None if quota == 'max' else _integer(quota)
        if quota == 0:
            raise ValueError('CPU quota is zero')
        cpu_stat = _counter_file(group / 'cpu.stat')
        for key in ('usage_usec', 'nr_throttled', 'throttled_usec'):
            if key not in cpu_stat:
                raise ValueError('Missing cpu.stat counter: ' + key)
        result['cpu'] = {'quota_us': quota, 'period_us': period,
                         'quota_cores': quota / period if quota is not None else None,
                         'affinity_cores': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
                         'affinity_cpus': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
                         'stat': cpu_stat}
        result['headroom_bytes'] = headroom
        if limits['max'] is None:
            raise ValueError('The current cgroup has no known finite memory.max bound')
        result['status'] = 'KNOWN_BOUNDED'
    except (OSError, ValueError, TypeError, StopIteration) as error:
        result['errors'].append(str(error))
    return result


def snapshot_counters(snapshot):
    result = {}
    for component, names in (('memory', ('events', 'stat')), ('cpu', ('stat',))):
        for name in names:
            for key, value in snapshot.get(component, {}).get(name, {}).items():
                result[component + '.' + name + '.' + key] = value
    return result


def counter_deltas(before, after):
    left, right = snapshot_counters(before), snapshot_counters(after)
    deltas, errors = {}, []
    for name in sorted(set(left) | set(right)):
        if name not in left or name not in right:
            errors.append('Resource counter became unavailable: ' + name)
        else:
            deltas[name] = right[name] - left[name]
            if name in PRESSURE_COUNTERS and deltas[name] < 0:
                errors.append('Pressure counter reset during campaign: ' + name)
    return deltas, errors


def _roots(roots):
    accepted, rejected = [], []
    for item in roots:
        path = Path(item)
        reason = None
        if not path.is_absolute() or path in FORBIDDEN_ROOTS:
            reason = 'An explicit task-specific absolute root is required'
        else:
            try:
                for ancestor in (path, *path.parents):
                    if ancestor.is_symlink():
                        raise ValueError('Reclaim roots must not contain symbolic links')
                if not path.is_dir():
                    raise ValueError('Reclaim root is not a directory')
                path = path.resolve(strict=True)
                if path in FORBIDDEN_ROOTS or any(Path(base) == path or Path(base) in path.parents for base in ('/proc','/sys','/dev')):
                    raise ValueError('Reclaim root resolves to a broad or virtual-system directory')
            except (OSError, ValueError) as error:
                reason = str(error)
        if reason:
            rejected.append({'path': str(item), 'reason': reason})
        elif path not in accepted:
            accepted.append(path)
    # A child of an already explicit root is redundant, never extra authority.
    accepted = [p for p in accepted if not any(parent in p.parents for parent in accepted if parent != p)]
    return accepted, rejected


def _reclaim_direct(roots, max_seconds, max_files, max_advised_bytes, min_file_bytes, min_file_age_seconds):
    started = time.monotonic()
    report = {'status': 'COMPLETE', 'roots': [str(p) for p in roots], 'advised_file_bytes': 0,
              'advised_files': 0, 'visited_files': 0, 'visited_directories': 0,
              'skipped_small_files': 0, 'skipped_recent_files': 0, 'skipped_nonregular': 0,
              'skipped_duplicate_inodes': 0, 'metadata_changes': 0, 'errors': [],
              'timed_out': False, 'contents_modified': False,
              'method': 'O_RDONLY|O_NOFOLLOW and POSIX_FADV_DONTNEED; no file content reads or writes',
              'kernel_scope': 'Only explicit regular-file mappings; discard is advisory and need not reclaim dirty/mapped/tmpfs pages'}
    seen = set()
    deadline = started + max_seconds
    now = time.time()

    def error(path, detail):
        if len(report['errors']) < 32:
            report['errors'].append({'path': str(path), 'reason': str(detail)})

    def budget():
        if time.monotonic() > deadline:
            report.update(status='PARTIAL', timed_out=True, stop_reason='traversal_time_limit')
            return False
        if report['visited_files'] >= max_files:
            report.update(status='PARTIAL', stop_reason='file_count_limit')
            return False
        return True

    def walk(directory, label):
        if not budget():
            return
        report['visited_directories'] += 1
        with os.scandir(directory) as entries:
            for entry in entries:
                if not budget():
                    return
                child = label / entry.name
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode):
                        nested = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                        try:
                            walk(nested, child)
                        finally:
                            os.close(nested)
                    elif stat.S_ISREG(mode):
                        report['visited_files'] += 1
                        fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
                        try:
                            before = os.fstat(fd)
                            if not stat.S_ISREG(before.st_mode):
                                report['skipped_nonregular'] += 1
                                continue
                            identity = (before.st_dev, before.st_ino)
                            if identity in seen:
                                report['skipped_duplicate_inodes'] += 1
                                continue
                            seen.add(identity)
                            if before.st_size < min_file_bytes:
                                report['skipped_small_files'] += 1
                                continue
                            if now - max(before.st_mtime, before.st_ctime) < min_file_age_seconds:
                                report['skipped_recent_files'] += 1
                                continue
                            if before.st_size + report['advised_file_bytes'] > max_advised_bytes:
                                report.update(status='PARTIAL', stop_reason='advised_byte_limit')
                                return
                            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                            report['advised_files'] += 1
                            report['advised_file_bytes'] += before.st_size
                            after = os.fstat(fd)
                            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                                report['metadata_changes'] += 1
                                error(child, 'File metadata changed concurrently; helper performed no content write')
                        finally:
                            os.close(fd)
                    else:
                        report['skipped_nonregular'] += 1
                except OSError as exception:
                    error(child, exception)
    for root in roots:
        if not budget() or report.get('stop_reason') == 'advised_byte_limit':
            break
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                walk(fd, root)
            finally:
                os.close(fd)
        except OSError as exception:
            error(root, exception)
    if report['errors'] and report['status'] == 'COMPLETE':
        report['status'] = 'PARTIAL'
    report['duration_ms'] = round((time.monotonic() - started) * 1000)
    return report


def _reclaim_worker(connection, roots, options):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        connection.send(_reclaim_direct(roots, **options))
    except BaseException as error:
        connection.send({'status': 'ERROR', 'errors': [str(error)], 'timed_out': False})
    finally:
        connection.close()


def reclaim_task_cache(roots, max_seconds=20, max_files=200000, max_advised_bytes=64 * GIB,
                       min_file_bytes=1024 ** 2, min_file_age_seconds=60):
    wrapper_started=time.monotonic()
    accepted, rejected = _roots(roots)
    options = {'max_seconds': max_seconds, 'max_files': max_files, 'max_advised_bytes': max_advised_bytes,
               'min_file_bytes': min_file_bytes, 'min_file_age_seconds': min_file_age_seconds}
    report = {'status': 'NOT_REQUESTED', 'roots': [str(p) for p in accepted], 'rejected_roots': rejected,
              'options': options, 'timed_out': False, 'contents_modified': False, 'advised_file_bytes': 0,
              'advised_files': 0, 'errors': [], 'duration_ms':0}
    if rejected:
        report['status'] = 'BLOCKED'
        return report
    if not accepted:
        return report
    if (not hasattr(os, 'posix_fadvise') or not hasattr(os, 'POSIX_FADV_DONTNEED') or
            max_seconds <= 0 or max_files < 1 or max_advised_bytes < 1 or min_file_bytes < 0 or min_file_age_seconds < 0):
        report.update(status='BLOCKED', errors=['DONTNEED is unsupported or traversal limits are invalid'])
        return report
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_reclaim_worker, args=(sender, accepted, options))
    worker.start();sender.close()
    try:
        if receiver.poll(max_seconds + .25):
            try:
                result = receiver.recv()
            except EOFError:
                result = {'status': 'ERROR', 'errors': ['Reclaim worker exited without a receipt'], 'timed_out': False}
            report.update(result)
        else:
            report.update(status='BLOCKED', timed_out=True, errors=['Reclaim worker exceeded its bounded deadline'])
    finally:
        receiver.close()
        worker.join(timeout=.25)
        if worker.is_alive():
            worker.terminate();worker.join(timeout=.25)
        if worker.is_alive():
            worker.kill();worker.join(timeout=.25)
        report['worker_still_running'] = worker.is_alive()
        report['worker_exit_code'] = worker.exitcode
        if worker.is_alive() or worker.exitcode not in (0, None):
            report['status'] = 'BLOCKED'
    report['total_duration_ms']=round((time.monotonic()-wrapper_started)*1000)
    return report


def _assessment(snapshot, threshold):
    reasons = []
    if snapshot.get('status') != 'KNOWN_BOUNDED':
        reasons.append('Resource limits/counters are unknown: ' + '; '.join(snapshot.get('errors', [])))
    elif snapshot['headroom_bytes'] < threshold:
        reasons.append('Observed cgroup headroom is below the declared resource contract')
    return reasons


def run_resource_preflight(profile='qualification', reclaim_roots=(), min_headroom_bytes=MIN_START_HEADROOM_BYTES,
                           min_end_headroom_bytes=MIN_END_HEADROOM_BYTES, output=None, max_seconds=20,
                           max_files=200000, max_advised_bytes=64 * GIB, min_file_bytes=1024 ** 2,
                           min_file_age_seconds=60, cgroup_root=None, cpu_scheduling=None):
    before = resource_snapshot(cgroup_root)
    reclamation = reclaim_task_cache(reclaim_roots, max_seconds, max_files, max_advised_bytes,
                                     min_file_bytes, min_file_age_seconds)
    after = resource_snapshot(cgroup_root)
    reasons = _assessment(after, min_headroom_bytes)
    if reclamation.get('status') in ('BLOCKED', 'ERROR') or reclamation.get('timed_out'):
        reasons.append('Scoped cache preparation did not complete within its resource/authorization bounds')
    if before.get('status') != 'KNOWN_BOUNDED':
        reasons.append('The initial resource observation was incomplete')
    if profile == 'qualification' and (min_headroom_bytes < MIN_START_HEADROOM_BYTES or min_end_headroom_bytes < MIN_END_HEADROOM_BYTES):
        reasons.append('Qualification resource thresholds are below the fixed minimum contract')
    assessment = 'BLOCKED' if reasons else 'PASS'
    deltas, counter_errors = counter_deltas(before, after)
    configuration = {'profile':profile, 'reclaim_roots':[str(Path(path)) for path in reclaim_roots],
                     'min_headroom_bytes':min_headroom_bytes, 'min_end_headroom_bytes':min_end_headroom_bytes,
                     'max_seconds':max_seconds, 'max_files':max_files, 'max_advised_bytes':max_advised_bytes,
                     'min_file_bytes':min_file_bytes, 'min_file_age_seconds':min_file_age_seconds,
                     'pressure_counter_policy':list(PRESSURE_COUNTERS),
                     'method':'explicit_readonly_regular_file_DONTNEED'}
    scheduling = None
    if cpu_scheduling is not None:
        configuration['cpu_scheduling'] = cpu_scheduling
        scheduling = observe_cpu_scheduling(cpu_scheduling)
        reasons.extend(scheduling['reasons'])
        if any(after.get('cpu', {}).get(key) != cpu_scheduling[key] for key in ('quota_us', 'period_us')):
            reasons.append('CPU quota changed after the campaign scheduling policy was selected')
        assessment = 'BLOCKED' if reasons else 'PASS'
    report = {'schema': SCHEMA, 'phase': 'preflight', 'profile': profile, 'configuration':configuration,
              'status': assessment if profile == 'qualification' else 'RECORDED_NONPROMOTABLE',
              'assessment': assessment, 'promotion_eligible': profile == 'qualification' and assessment == 'PASS',
              'scope': 'Host resource admission only; no radio, RF or runtime result is changed',
              'min_headroom_bytes': min_headroom_bytes, 'min_end_headroom_bytes': min_end_headroom_bytes,
              'pressure_counter_policy': list(PRESSURE_COUNTERS), 'before': before, 'after': after,
              'headroom_bytes': after.get('headroom_bytes'), 'reclaim': reclamation, 'reasons': reasons,
              'preparation_counter_deltas': deltas, 'preparation_counter_errors': counter_errors,
              'observed_memory_reduction_bytes': max(0, before.get('memory', {}).get('current_bytes', 0) - after.get('memory', {}).get('current_bytes', 0)),
              'observed_clean_file_cache_reduction_bytes': max(0, before.get('memory', {}).get('clean_file_cache_estimate_bytes', 0) - after.get('memory', {}).get('clean_file_cache_estimate_bytes', 0))}
    if scheduling is not None:
        report['cpu_scheduling'] = scheduling
    _save(output, report)
    return report


def observe_resource_end(preflight, profile=None, output=None, cgroup_root=None):
    profile = profile or preflight.get('profile', 'qualification')
    baseline = preflight.get('after', {})
    after = resource_snapshot(cgroup_root)
    threshold = preflight.get('min_end_headroom_bytes', MIN_END_HEADROOM_BYTES)
    reasons = _assessment(after, threshold)
    if (preflight.get('schema') != SCHEMA or preflight.get('phase') != 'preflight' or
            preflight.get('profile') != profile or preflight.get('configuration',{}).get('profile') != profile):
        reasons.append('The end observation does not match its declared preflight/profile')
    if profile == 'qualification' and threshold < MIN_END_HEADROOM_BYTES:
        reasons.append('End resource threshold is below the fixed qualification minimum')
    deltas, errors = counter_deltas(baseline, after)
    if baseline.get('cgroup_path') != after.get('cgroup_path'):
        reasons.append('The observed cgroup changed during the campaign')
    for component, keys in (('memory',('max_bytes','high_bytes','effective_limit_bytes')), ('cpu',('quota_us','period_us'))):
        if any(baseline.get(component,{}).get(key) != after.get(component,{}).get(key) for key in keys):
            reasons.append('Resource limits changed during the campaign: '+component)
    if preflight.get('assessment') != 'PASS':
        reasons.append('The initial resource prerequisite was not satisfied')
    if errors:
        reasons.extend(errors)
    pressure = {name: deltas.get(name) for name in PRESSURE_COUNTERS if deltas.get(name, 0) > 0}
    if pressure:
        reasons.append('New cgroup memory pressure occurred during the campaign')
    scheduling = None
    cpu_policy = preflight.get('configuration', {}).get('cpu_scheduling')
    if cpu_policy is not None:
        scheduling = observe_cpu_scheduling(cpu_policy)
        reasons.extend(scheduling['reasons'])
    assessment = 'BLOCKED' if reasons else 'PASS'
    report = {'schema': SCHEMA, 'phase': 'end', 'profile': profile, 'configuration':preflight.get('configuration',{}),
              'status': assessment if profile == 'qualification' else 'RECORDED_NONPROMOTABLE',
              'assessment': assessment, 'promotion_eligible': profile == 'qualification' and assessment == 'PASS',
              'scope': 'Resource observation is independent of the original runtime/campaign verdict',
              'min_headroom_bytes': threshold, 'before': baseline, 'after': after,
              'headroom_bytes': after.get('headroom_bytes'), 'counter_deltas': deltas,
              'new_memory_pressure': pressure, 'reasons': reasons,
              'cpu_throttling_is_diagnostic_only': True}
    if scheduling is not None:
        report['cpu_scheduling'] = scheduling
    _save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('qualification', 'regression', 'durability'), default='qualification')
    parser.add_argument('--phase', choices=('preflight', 'end'), default='preflight')
    parser.add_argument('--reclaim-root', type=Path, action='append', default=[])
    parser.add_argument('--min-headroom-bytes', type=int, default=MIN_START_HEADROOM_BYTES)
    parser.add_argument('--min-end-headroom-bytes', type=int, default=MIN_END_HEADROOM_BYTES)
    parser.add_argument('--max-seconds', type=float, default=20)
    parser.add_argument('--max-files', type=int, default=200000)
    parser.add_argument('--min-file-bytes', type=int, default=1024 ** 2)
    parser.add_argument('--min-file-age-seconds', type=float, default=60)
    parser.add_argument('--cgroup-root', type=Path)
    parser.add_argument('--preflight-json', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.phase == 'end':
        if not args.preflight_json:
            parser.error('--phase end requires --preflight-json')
        report = observe_resource_end(json.loads(args.preflight_json.read_text()), args.profile, args.output, args.cgroup_root)
    else:
        report = run_resource_preflight(args.profile, args.reclaim_root, args.min_headroom_bytes,
                                       args.min_end_headroom_bytes, args.output, args.max_seconds, args.max_files,
                                       min_file_bytes=args.min_file_bytes, min_file_age_seconds=args.min_file_age_seconds,
                                       cgroup_root=args.cgroup_root)
    print(json.dumps({key: report[key] for key in ('status', 'assessment', 'phase', 'headroom_bytes', 'reasons')}, indent=2))
    return 1 if args.profile == 'qualification' and report['status'] != 'PASS' else 0


if __name__ == '__main__':
    raise SystemExit(main())
