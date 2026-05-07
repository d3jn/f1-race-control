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

HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

CAR_MOTION_FORMAT = "<6f6h6f"
CAR_MOTION_SIZE = struct.calcsize(CAR_MOTION_FORMAT)

PARTICIPANT_FORMAT = f"<7B{NAME_LEN}s2BH2B12B"
PARTICIPANT_SIZE = struct.calcsize(PARTICIPANT_FORMAT)

SESSION_TRACK_ID_OFFSET = HEADER_SIZE + 7

TRACK_NAMES = {
    0: "melbourne",
    2: "shanghai",
    3: "bahrain",
    4: "catalunya",
    5: "monaco",
    6: "montreal",
    7: "silverstone",
    9: "hungaroring",
    10: "spa",
    11: "monza",
    12: "singapore",
    13: "suzuka",
    14: "abu_dhabi",
    15: "texas",
    16: "brazil",
    17: "austria",
    19: "mexico",
    20: "baku",
    26: "zandvoort",
    27: "imola",
    29: "jeddah",
    30: "miami",
    31: "las_vegas",
    32: "losail",
    39: "silverstone_reverse",
    40: "austria_reverse",
    41: "zandvoort_reverse",
}

if getattr(sys, "frozen", False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_base_dir, "settings.json"), "r") as _f:
    _settings = json.load(_f)
UDP_PORT = int(_settings["udp_port"])
DEBUG_MODE = bool(_settings.get("debug_mode", False))


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


class Tracker:
    def __init__(self, debug=False):
        self.debug = debug
        self.num_active = 0
        self.driver_names = [""] * NUM_CARS
        self.motion = [None] * NUM_CARS
        self.player_idx = None
        self.frame_id = 0
        self.session_time = 0.0
        self.track_id = -1
        self._last_print = 0.0

    def update_participants(self, num_active, participants):
        self.num_active = num_active
        for i, p in enumerate(participants):
            self.driver_names[i] = p["name"] if i < num_active else ""

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

    def maybe_print(self, interval=1.0):
        if not self.debug:
            return
        now = time.monotonic()
        if now - self._last_print < interval:
            return
        self._last_print = now
        idx = self.player_idx
        if idx is None:
            return
        m = self.motion[idx]
        if m is None:
            return
        name = self.driver_names[idx] or f"car{idx}"
        x, y, z = m["position"]
        vx, vy, vz = m["velocity"]
        fx, fy, fz = m["forward"]
        rx, ry, rz = m["right"]
        gl, gn, gv = m["gForce"]
        yaw_deg = math.degrees(m["yaw"])
        pitch_deg = math.degrees(m["pitch"])
        roll_deg = math.degrees(m["roll"])
        clock = time.strftime("%H:%M:%S")
        print(f"[{clock}] frame {self.frame_id} t={self.session_time:6.1f}s  {name} (car {idx})")
        print(f"  pos     = ({x:>+9.2f}, {y:>+8.2f}, {z:>+9.2f}) m")
        print(f"  vel     = ({vx:>+9.2f}, {vy:>+8.2f}, {vz:>+9.2f}) m/s   speed = {self.speed_kmh(idx):>6.1f} km/h")
        print(f"  forward = ({fx:>+9.4f}, {fy:>+8.4f}, {fz:>+9.4f})")
        print(f"  right   = ({rx:>+9.4f}, {ry:>+8.4f}, {rz:>+9.4f})")
        print(f"  yaw     = {yaw_deg:>+7.2f} deg   pitch = {pitch_deg:>+6.2f} deg   roll = {roll_deg:>+6.2f} deg")
        print(f"  gforce  = lat {gl:>+5.2f}   lon {gn:>+5.2f}   vert {gv:>+5.2f}")


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
        slug = TRACK_NAMES.get(track_id, "unknown")
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
        print(f"[log] new file: {os.path.basename(path)}")

    def append(self, record):
        if self.current_handle is None:
            return
        self.current_handle.write(json.dumps(record, separators=(",", ":")) + "\n")

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
                print("[log] action 3 ignored: no motion data yet")
                return
            self.append(record)
            print(f"[log] sample written ({os.path.basename(self.current_path)})")


def main():
    tracker = Tracker(debug=DEBUG_MODE)
    logger = Logger(_base_dir, debug=DEBUG_MODE)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening for F1 25 telemetry on {UDP_IP}:{UDP_PORT}...")
    if DEBUG_MODE:
        print("debug_mode=true: UDP Action 4 = new log file, UDP Action 3 = append sample")
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
                tracker.maybe_print()
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
