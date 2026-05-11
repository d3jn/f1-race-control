import datetime
import json
import math
import os
import socket
import struct
import sys
import time

UDP_IP = "0.0.0.0"
NUM_CARS = 22
NAME_LEN = 32
DIR_SCALE = 1.0 / 32767.0

PACKET_ID_MOTION = 0
PACKET_ID_SESSION = 1
PACKET_ID_EVENT = 3
PACKET_ID_PARTICIPANTS = 4

UDP_ACTION_3 = 0x00400000
UDP_ACTION_4 = 0x00800000
UDP_ACTION_5 = 0x01000000

HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

CAR_MOTION_FORMAT = "<6f6h6f"
CAR_MOTION_SIZE = struct.calcsize(CAR_MOTION_FORMAT)

PARTICIPANT_FORMAT = f"<7B{NAME_LEN}s2BH2B12B"
PARTICIPANT_SIZE = struct.calcsize(PARTICIPANT_FORMAT)

SESSION_TRACK_ID_OFFSET = HEADER_SIZE + 7

TRACK_IDS = {
    "Melbourne": 0,
    "Shanghai": 2,
    "Bahrain": 3,
    "Catalunya": 4,
    "Monaco": 5,
    "Montreal": 6,
    "Silverstone": 7,
    "Hungaroring": 9,
    "Spa": 10,
    "Monza": 11,
    "Singapore": 12,
    "Suzuka": 13,
    "Abu Dhabi": 14,
    "Texas": 15,
    "Brazil": 16,
    "Austria": 17,
    "Mexico": 19,
    "Baku": 20,
    "Zandvoort": 26,
    "Imola": 27,
    "Jeddah": 29,
    "Miami": 30,
    "Las Vegas": 31,
    "Losail": 32,
    "Silverstone Reverse": 39,
    "Austria Reverse": 40,
    "Zandvoort Reverse": 41,
}
TRACK_NAMES = {v: k for k, v in TRACK_IDS.items()}

TEAM_IDS = {
    "F1 2025 Mercedes": 0,
    "F1 2025 Ferrari": 1,
    "F1 2025 Red Bull Racing": 2,
    "F1 2025 Williams": 3,
    "F1 2025 Aston Martin": 4,
    "F1 2025 Alpine": 5,
    "F1 2025 RB": 6,
    "F1 2025 Haas": 7,
    "F1 2025 McLaren": 8,
    "F1 2025 Sauber": 9,
    "F1 World Car": 41,
    "F1 Custom Team": 104,
    "Konnersport": 129,
    "APXGP 2024": 142,
    "APXGP 2025": 154,
    "Konnersport 2024": 155,
    "F2 2024 Art GP": 158,
    "F2 2024 Campos": 159,
    "F2 2024 Rodin Motorsport": 160,
    "F2 2024 AIX Racing": 161,
    "F2 2024 DAMS": 162,
    "F2 2024 Hitech": 163,
    "F2 2024 MP Motorsport": 164,
    "F2 2024 Prema": 165,
    "F2 2024 Trident": 166,
    "F2 2024 Van Amersfoort Racing": 167,
    "F2 2024 Invicta": 168,
    "F1 2024 Mercedes": 185,
    "F1 2024 Ferrari": 186,
    "F1 2024 Red Bull Racing": 187,
    "F1 2024 Williams": 188,
    "F1 2024 Aston Martin": 189,
    "F1 2024 Alpine": 190,
    "F1 2024 RB": 191,
    "F1 2024 Haas": 192,
    "F1 2024 McLaren": 193,
    "F1 2024 Sauber": 194,
}

if getattr(sys, "frozen", False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_base_dir, "settings.json"), "r") as _f:
    _settings = json.load(_f)
UDP_PORT = int(_settings["udp_port"])
DEBUG_MODE = bool(_settings.get("debug_mode", False))

CAR_EXTENTS = {}
for _name, _car in _settings.get("cars", {}).items():
    if _name not in TEAM_IDS:
        raise SystemExit(f"settings.json: cars key {_name!r} not in TEAM_IDS")
    _length = float(_car["length"])
    _width = float(_car["width"])
    _off_long = float(_car["offset_longitudinal"])
    _off_lat = float(_car["offset_lateral"])
    CAR_EXTENTS[TEAM_IDS[_name]] = (
        _length / 2 - _off_long,  # forward
        _length / 2 + _off_long,  # rear
        _width / 2 + _off_lat,    # left
        _width / 2 - _off_lat,    # right
    )

PIT_LINES = {}
for _name, _lines in _settings.get("pit_lines", {}).items():
    if _name not in TRACK_IDS:
        raise SystemExit(f"settings.json: pit_lines key {_name!r} not in TRACK_IDS")
    PIT_LINES[TRACK_IDS[_name]] = {
        "entry": [tuple(p) for p in _lines.get("entry", [])],
        "exit": [tuple(p) for p in _lines.get("exit", [])],
    }


def parse_header(data):
    f = struct.unpack_from(HEADER_FORMAT, data, 0)
    return {
        "packetFormat": f[0],
        "packetId": f[5],
        "sessionUID": f[6],
        "sessionTime": f[7],
        "frameIdentifier": f[8],
        "playerCarIndex": f[10],
    }


def parse_motion(data):
    cars = []
    for i in range(NUM_CARS):
        f = struct.unpack_from(CAR_MOTION_FORMAT, data, HEADER_SIZE + i * CAR_MOTION_SIZE)
        cars.append({
            "position": (f[0], f[1], f[2]),
            "velocity": (f[3], f[4], f[5]),
            "forward": (f[6] * DIR_SCALE, f[7] * DIR_SCALE, f[8] * DIR_SCALE),
            "right": (f[9] * DIR_SCALE, f[10] * DIR_SCALE, f[11] * DIR_SCALE),
            "gForce": (f[12], f[13], f[14]),
            "yaw": f[15],
            "pitch": f[16],
            "roll": f[17],
        })
    return cars


def parse_participants(data):
    num_active = data[HEADER_SIZE]
    participants = []
    base = HEADER_SIZE + 1
    for i in range(NUM_CARS):
        f = struct.unpack_from(PARTICIPANT_FORMAT, data, base + i * PARTICIPANT_SIZE)
        name = f[7].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        participants.append({
            "aiControlled": f[0],
            "teamId": f[3],
            "raceNumber": f[5],
            "name": name,
        })
    return num_active, participants


def parse_session_track_id(data):
    if len(data) <= SESSION_TRACK_ID_OFFSET:
        return -1
    return struct.unpack_from("<b", data, SESSION_TRACK_ID_OFFSET)[0]


def parse_event_button_status(data):
    if len(data) < HEADER_SIZE + 4 + 4:
        return None
    if bytes(data[HEADER_SIZE:HEADER_SIZE + 4]) != b"BUTN":
        return None
    return struct.unpack_from("<I", data, HEADER_SIZE + 4)[0]


def segment_intersects_aabb(x1, y1, x2, y2, x_min, x_max, y_min, y_max):
    dx, dy = x2 - x1, y2 - y1
    t_min, t_max = 0.0, 1.0
    for p, q in ((-dx, x1 - x_min), (dx, x_max - x1), (-dy, y1 - y_min), (dy, y_max - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t_max:
                    return False
                if t > t_min:
                    t_min = t
            else:
                if t < t_min:
                    return False
                if t < t_max:
                    t_max = t
    return True


def rectangle_intersects_polyline(pivot_xz, forward_xz, right_xz, polyline, extents):
    if len(polyline) < 2:
        return False
    px, pz = pivot_xz
    fx, fz = forward_xz
    rx, rz = right_xz
    forward_ext, rear_ext, left_ext, right_ext = extents
    x_min, x_max = -rear_ext, forward_ext
    y_min, y_max = -left_ext, right_ext
    prev_x = (polyline[0][0] - px) * fx + (polyline[0][1] - pz) * fz
    prev_y = (polyline[0][0] - px) * rx + (polyline[0][1] - pz) * rz
    for i in range(1, len(polyline)):
        cur_x = (polyline[i][0] - px) * fx + (polyline[i][1] - pz) * fz
        cur_y = (polyline[i][0] - px) * rx + (polyline[i][1] - pz) * rz
        if not ((prev_x < x_min and cur_x < x_min)
                or (prev_x > x_max and cur_x > x_max)
                or (prev_y < y_min and cur_y < y_min)
                or (prev_y > y_max and cur_y > y_max)):
            if segment_intersects_aabb(prev_x, prev_y, cur_x, cur_y, x_min, x_max, y_min, y_max):
                return True
        prev_x, prev_y = cur_x, cur_y
    return False


def car_pit_line_hits(team_id, track_id, position, forward, right):
    extents = CAR_EXTENTS.get(team_id)
    lines = PIT_LINES.get(track_id)
    if not extents or not lines:
        return []
    fx, _, fz = forward
    rx, _, rz = right
    f_mag = math.hypot(fx, fz)
    r_mag = math.hypot(rx, rz)
    if f_mag == 0 or r_mag == 0:
        return []
    forward_xz = (fx / f_mag, fz / f_mag)
    right_xz = (rx / r_mag, rz / r_mag)
    pivot_xz = (position[0], position[2])
    return [
        name for name, points in lines.items()
        if points and rectangle_intersects_polyline(pivot_xz, forward_xz, right_xz, points, extents)
    ]


class Tracker:
    def __init__(self, debug=False):
        self.debug = debug
        self.num_active = 0
        self.driver_names = [""] * NUM_CARS
        self.team_ids = [-1] * NUM_CARS
        self.motion = [None] * NUM_CARS
        self.player_idx = None
        self.frame_id = 0
        self.session_time = 0.0
        self.track_id = -1
        self._last_line_print = 0.0

    def update_participants(self, num_active, participants):
        self.num_active = num_active
        for i, p in enumerate(participants):
            if i < num_active:
                self.driver_names[i] = p["name"]
                self.team_ids[i] = p["teamId"]
            else:
                self.driver_names[i] = ""
                self.team_ids[i] = -1

    def update_motion(self, header, cars):
        self.player_idx = header["playerCarIndex"]
        self.frame_id = header["frameIdentifier"]
        self.session_time = header["sessionTime"]
        self.motion = cars

    def update_track(self, track_id):
        self.track_id = track_id

    def speed_kmh(self, idx):
        m = self.motion[idx]
        if m is None:
            return 0.0
        vx, vy, vz = m["velocity"]
        return math.sqrt(vx * vx + vy * vy + vz * vz) * 3.6

    def player_record(self):
        idx = self.player_idx
        if idx is None:
            return None
        m = self.motion[idx]
        if m is None:
            return None
        return {
            "timestamp": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "frame": self.frame_id,
            "session_time": round(self.session_time, 3),
            "track_id": self.track_id,
            "track_name": TRACK_NAMES.get(self.track_id, "unknown"),
            "car_index": idx,
            "driver_name": self.driver_names[idx],
            "position": list(m["position"]),
            "velocity": list(m["velocity"]),
            "speed_kmh": round(self.speed_kmh(idx), 3),
            "forward": list(m["forward"]),
            "right": list(m["right"]),
            "yaw": m["yaw"],
            "pitch": m["pitch"],
            "roll": m["roll"],
            "g_force": list(m["gForce"]),
        }

    def check_pit_lines(self, interval=1.0):
        if not self.debug:
            return
        idx = self.player_idx
        if idx is None:
            return
        m = self.motion[idx]
        if m is None:
            return
        hits = car_pit_line_hits(self.team_ids[idx], self.track_id, m["position"], m["forward"], m["right"])
        if not hits:
            return
        now = time.monotonic()
        if now - self._last_line_print < interval:
            return
        self._last_line_print = now
        for name in hits:
            print(f"[line] car is over the {name} pit line")


class Logger:
    def __init__(self, base_dir, debug=False):
        self.debug = debug
        self.logs_dir = os.path.join(base_dir, "logs")
        self.current_path = None
        self.current_handle = None
        self.prev_buttons = 0

    def close(self):
        if self.current_handle is not None:
            self.current_handle.close()
            self.current_handle = None
            self.current_path = None

    def _next_path(self, track_id):
        os.makedirs(self.logs_dir, exist_ok=True)
        slug = TRACK_NAMES.get(track_id, "unknown").lower().replace(" ", "_")
        date = time.strftime("%Y_%m_%d")
        prefix = f"{date}_{slug}_"
        used = set()
        for fn in os.listdir(self.logs_dir):
            if fn.startswith(prefix) and fn.endswith(".txt"):
                try:
                    used.add(int(fn[len(prefix):-len(".txt")]))
                except ValueError:
                    pass
        nn = 1
        while nn in used:
            nn += 1
        return os.path.join(self.logs_dir, f"{prefix}{nn:02d}.txt")

    def start_new_file(self, track_id):
        self.close()
        path = self._next_path(track_id)
        self.current_path = path
        self.current_handle = open(path, "w", buffering=1)

    def append(self, record):
        if self.current_handle is None:
            return
        self.current_handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def append_xz(self, x, z):
        if self.current_handle is None:
            return
        self.current_handle.write(f"[{x:.2f}, {z:.2f}],\n")

    def handle_buttons(self, button_status, tracker):
        if not self.debug:
            return
        rising = button_status & ~self.prev_buttons
        self.prev_buttons = button_status
        if rising & UDP_ACTION_4:
            self.start_new_file(tracker.track_id)
        if rising & UDP_ACTION_3:
            if self.current_handle is None:
                self.start_new_file(tracker.track_id)
            record = tracker.player_record()
            if record is None:
                return
            self.append(record)
        if rising & UDP_ACTION_5:
            idx = tracker.player_idx
            if idx is None or tracker.motion[idx] is None:
                return
            if self.current_handle is None:
                self.start_new_file(tracker.track_id)
            pos = tracker.motion[idx]["position"]
            self.append_xz(pos[0], pos[2])


def main():
    tracker = Tracker(debug=DEBUG_MODE)
    logger = Logger(_base_dir, debug=DEBUG_MODE)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening for F1 25 telemetry on {UDP_IP}:{UDP_PORT}...")
    try:
        while True:
            data, _ = sock.recvfrom(2048)
            if len(data) < HEADER_SIZE:
                continue
            header = parse_header(data)
            pid = header["packetId"]
            if pid == PACKET_ID_MOTION:
                if len(data) < HEADER_SIZE + NUM_CARS * CAR_MOTION_SIZE:
                    continue
                tracker.update_motion(header, parse_motion(data))
                tracker.check_pit_lines()
            elif pid == PACKET_ID_PARTICIPANTS:
                if len(data) < HEADER_SIZE + 1 + NUM_CARS * PARTICIPANT_SIZE:
                    continue
                num_active, participants = parse_participants(data)
                tracker.update_participants(num_active, participants)
            elif pid == PACKET_ID_SESSION:
                tracker.update_track(parse_session_track_id(data))
            elif pid == PACKET_ID_EVENT:
                bs = parse_event_button_status(data)
                if bs is not None:
                    logger.handle_buttons(bs, tracker)
    finally:
        logger.close()


if __name__ == "__main__":
    main()
