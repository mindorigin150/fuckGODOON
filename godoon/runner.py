"""Execute one run and require a saved valid record plus an activity credit."""
from datetime import datetime, timedelta, timezone
import json
import math
import platform
import signal
import threading
import time

from .api import CLUB_ID
from .auth import authenticated_client
from .geometry import Track, distance

ZONE = timezone(timedelta(hours=8))
DEVICE = f'fuckGODOON Python {platform.python_version()} {platform.system()}'


def school(client):
    return client.request('GET', '/v1/club_custom_activity/48472/detail', {'club_id': CLUB_ID})


def route_config(client):
    return client.request('GET', '/v1/route/get_route_config', {'club_id': CLUB_ID})


def daily_credit(client, challenge_id):
    day = datetime.now(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    result = client.request('GET', '/v1/club_challenge_member/challenge_member_daily_score',
                            {'club_id': CLUB_ID, 'club_challenge_id': challenge_id,
                             'club_user_id': 0, 'cur_day': int(day.timestamp())})
    if result['cur_day'] != day.strftime('%Y-%m-%d'):
        raise RuntimeError('活动接口返回的日期与今天不一致。')
    # The template's today_is_complete flag is not the member's actual credit ledger.
    return sum(row['score'] for row in result['run_score_list'])


def validate_record(record, minimum):
    if record['total_length'] < minimum or record['is_fraud'] or record['time_duplicate']:
        raise RuntimeError('服务端记录距离不足或被标记为异常，不能判定成功。')


def saved_record(client, route_id, minimum):
    result = client.request('GET', '/v1/statistic/get_user_gps_info/list',
                            {'club_id': CLUB_ID, 'cursor': 1, 'sports_type': 1, 'month_time': 0})
    records = [row for row in result['list'] if row['route_id'] == route_id]
    if len(records) != 1:
        raise RuntimeError('尚未在记录列表找到本次运动。')
    validate_record(records[0], minimum)
    return {key: records[0][key] for key in ('total_length', 'total_time', 'pace', 'is_fraud', 'time_duplicate')}


def load_route(path):
    try:
        route = json.loads(path.read_text(encoding='utf-8'))
        if route['coordinate_system'] != 'WGS84' or len(route['points']) < 3:
            raise ValueError
        for latitude, longitude in route['points']:
            if (isinstance(latitude, bool) or isinstance(longitude, bool)
                    or not math.isfinite(latitude) or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
                raise ValueError
        for name in ('speed_mps', 'target_m'):
            if isinstance(route[name], bool) or not math.isfinite(route[name]) or route[name] <= 0:
                raise ValueError
        return route
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise RuntimeError('route.json 必须包含有效的 WGS84 路线、正数距离和速度。') from error


def point(position, stamp, previous, kind=0):
    segment = distance((previous['latitude'], previous['longitude']), position) if previous else 0
    elapsed = stamp - previous['time_stamp'] if previous else 0
    return {
        'distance': segment, 'elevation': 16, 'h_accuracy': 4, 'v_accuracy': 4,
        'latitude': position[0], 'longitude': position[1], 'time_stamp': stamp,
        'topreviouscostTime': elapsed, 'topreviousspeed': segment / (elapsed / 1000) if elapsed else 0,
        'to_start_dost_time': previous['to_start_dost_time'] + elapsed if previous else 0,
        'to_start_distance': previous['to_start_distance'] + segment if previous else 0,
        'type': kind, 'time_str': datetime.fromtimestamp(stamp / 1000, ZONE).strftime('%m.%d %H:%M:%S'),
    }


def complete(client, route_id, delete):
    return client.request('POST', '/v1/route/completes_route',
                          {'route_id': route_id, 'is_delete': delete, 'device_info': DEVICE})


def recover_owned_run(client, store, active):
    journal = store.load('run.json')
    if not active['route_id']:
        return
    if journal is None or journal['user_id'] != client.user_id or journal.get('route_id') != active['route_id']:
        raise RuntimeError('账号有其它未结束运动，请先在小程序中处理。')
    complete(client, active['route_id'], True)
    journal.update(state='cancelled', partial_record_deleted=True)
    store.save('run.json', journal)
    print('已清理本工具上次中断的未完成记录。', flush=True)


def wait_for_credit(client, store, state, stop):
    state['saved_record'] = saved_record(client, state['route_id'], state['minimum_m'])
    store.save('run.json', state)
    print('记录已保存，正在复查活动有效次数（最多约 31 分钟）。', flush=True)
    deadline = time.monotonic() + 1860
    while True:
        current = school(client)
        if current['total_steps'] > state['before_count']:
            state['saved_record'] = saved_record(client, state['route_id'], state['minimum_m'])
            state.update(state='verified', verified=True, after_count=current['total_steps'])
            store.save('run.json', state)
            print(f'打卡成功：{state["saved_record"]["total_length"]/1000:.2f} 公里，有效次数 '
                  f'{state["before_count"]} → {current["total_steps"]}。', flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('记录已保存，但有效次数尚未增加；请稍后在小程序检查。')
        if stop.wait(60):
            raise InterruptedError('已停止等待，保存的记录会保留。')


def run(client, store, route_path, stop):
    before = school(client)
    if daily_credit(client, before['club_challenge_id']) > 0:
        print('今天已完成有效打卡，不会重复创建记录。')
        return
    rules = client.request('GET', '/v1/club_challenge/challenge_detail',
                           {'club_id': CLUB_ID, 'id': before['club_challenge_id']})
    walking = [rule for rule in rules['score_rule'] if rule['score_type'] == 3 and rule['state'] == 1]
    if len(walking) != 1:
        raise RuntimeError('当前活动规则不在本版本支持范围内。')
    minimum = max(3000, walking[0]['base_value'])
    route = load_route(route_path)
    if route['target_m'] < minimum:
        raise RuntimeError(f'当前活动要求至少 {minimum} 米，请调整 route.json。')
    track = Track(route['points'])
    active = route_config(client)
    recover_owned_run(client, store, active)
    previous_run = store.load('run.json')
    today = datetime.now(ZONE).date().isoformat()
    if (previous_run is not None and previous_run['user_id'] == client.user_id
            and previous_run['date'] == today and previous_run['state'] == 'awaiting_count'):
        wait_for_credit(client, store, previous_run, stop)
        return
    state = {'user_id': client.user_id, 'date': today, 'state': 'starting', 'verified': False,
             'before_count': before['total_steps'], 'minimum_m': minimum, 'meters': 0, 'steps': 0}
    store.save('run.json', state)
    route_id = None
    completed = False
    try:
        if stop.is_set():
            raise InterruptedError('已停止。')
        response = client.request('POST', '/v1/route/start_route',
                                  {'is_in_room': 0, 'sports_type': 1, 'location': '',
                                   'source': 2, 'device_info': DEVICE})
        route_id = response['route_id']
        state.update(state='running', route_id=route_id)
        store.save('run.json', state)
        start_wall, start_clock = time.time(), time.monotonic()
        previous = point(track.position(0), round(start_wall * 1000), None)
        client.request('POST', '/v1/route/create_route_point', {'route_id': route_id, 'points': [previous]})
        print(f'已开始：{route["target_m"]/1000:.2f} 公里，预计约 '
              f'{route["target_m"]/route["speed_mps"]/60:.0f} 分钟。Ctrl+C 可停止。', flush=True)
        sent_steps, next_step_report, index = 0, 60, 1
        pending = []
        while previous['to_start_distance'] < route['target_m']:
            if stop.wait(max(0, start_clock + index * 5 - time.monotonic())):
                raise InterruptedError('已停止，正在清理本次未完成记录。')
            elapsed = time.monotonic() - start_clock
            previous = point(track.position(elapsed * route['speed_mps']), round((start_wall + elapsed) * 1000), previous)
            pending.append(previous)
            if len(pending) >= 5:
                client.request('POST', '/v1/route/create_route_point', {'route_id': route_id, 'points': pending})
                pending = []
            if elapsed >= next_step_report:
                steps = round(elapsed * 150 / 60)
                client.request('POST', '/v1/route/create_route_gyroscope_steps',
                               {'route_id': route_id, 'type': 0, 'cur_steps': steps - sent_steps,
                                'upload_time': int(time.time() // 60 * 60), 'dur': 20})
                sent_steps = steps
                next_step_report += 60
                if route_config(client)['route_id'] != route_id:
                    raise RuntimeError('服务端运动会话发生变化。')
                state.update(meters=round(previous['to_start_distance'], 1), steps=steps,
                             elapsed_seconds=round(elapsed))
                store.save('run.json', state)
                print(f'{state["meters"]/1000:.2f}/{route["target_m"]/1000:.2f} 公里 · '
                      f'{round(elapsed/60)} 分钟 · {steps} 步', flush=True)
            index += 1
        if pending:
            client.request('POST', '/v1/route/create_route_point', {'route_id': route_id, 'points': pending})
        elapsed = time.monotonic() - start_clock
        steps = round(elapsed * 150 / 60)
        client.request('POST', '/v1/route/create_route_gyroscope_steps',
                       {'route_id': route_id, 'type': 1, 'cur_steps': steps - sent_steps,
                        'upload_time': int(time.time()), 'dur': 20})
        client.request('POST', '/v1/route/create_route_point', {'route_id': route_id, 'points': [dict(previous, type=5)]})
        complete(client, route_id, False)
        completed = True
        state.update(state='awaiting_count', meters=round(previous['to_start_distance'], 1),
                     steps=steps, elapsed_seconds=round(elapsed))
        store.save('run.json', state)
        wait_for_credit(client, store, state, stop)
    except BaseException as error:
        state['error'] = str(error)
        if not completed:
            state['state'] = 'cancelled' if isinstance(error, InterruptedError) else 'failed'
            if route_id:
                try:
                    # An uncertain HTTP result must not delete an already completed record.
                    if route_config(client)['route_id'] == route_id:
                        complete(client, route_id, True)
                        state['partial_record_deleted'] = True
                except Exception as cleanup:
                    state['cleanup_error'] = str(cleanup)
        store.save('run.json', state)
        raise


def start(store, route_path):
    with store.lock:
        client = authenticated_client(store)
        stop = threading.Event()
        previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in previous_handlers:
                signal.signal(sig, lambda *_: stop.set())
            run(client, store, route_path, stop)
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            client.close()
