import time
import re
import os
import subprocess
import statistics
import asyncio
import argparse

import RPi.GPIO as GPIO
import speech_recognition as sr
import edge_tts

try:
    from RpiMotorLib import RpiMotorLib
except Exception:
    RpiMotorLib = None


AUDIO_INPUT_DEVICE = os.environ.get("AUDIO_INPUT_DEVICE", "hw:3,0")
AUDIO_OUTPUT_DEVICE = os.environ.get("AUDIO_OUTPUT_DEVICE", "hw:2,0")

WAKE_WAV_FILE = "wake_input.wav"
COMMAND_WAV_FILE = "command_input.wav"
TTS_FILE = "tts_output.mp3"

WAKE_RECORD_SECONDS = 3	
COMMAND_RECORD_SECONDS = 3
SAMPLE_RATE = 48000

TTS_VOICE = "ko-KR-SunHiNeural"
TTS_RATE = "-5%"
TTS_VOLUME = "+0%"
TTS_PITCH = "-2Hz"

SPEAKER_SCALE = 65536

DT_PIN = 5
SCK_PIN = 6

# LED 핀은 사용하지 않음: GPIO 충돌 방지를 위해 제거

BUZZER = 17

# ===== 추가 구동부 핀 설정 =====
# relay.py 기준: GPIO27 = 물리핀 13번, active_low 릴레이
SOLENOID_PIN = 27
SOLENOID_ACTIVE_LOW = False
SOLENOID_ON_SECONDS = 0.2
SOLENOID_OFF_SECONDS = 0.2

# nematest4.py 기준 NEMA17 + A4988 핀
MOTOR_DIRECTION_PIN = 20
MOTOR_STEP_PIN = 21
MOTOR_MODE_PINS = (14, 15, 18)
MOTOR_STEPTYPE = "1/8"
MOTOR_FULL_REV_STEPS = 1600
MOTOR_SLOT_STEPS = MOTOR_FULL_REV_STEPS // 5
MOTOR_HALF_SLOT_STEPS = MOTOR_SLOT_STEPS // 2
MOTOR_RUN_DELAY = 0.002

# nematest4.py에서 True=시계 방향, False=반시계 방향 기준
CLOCKWISE = True
COUNTERCLOCKWISE = False

# 5개 조미료 위치 배정
# 초기 위치: 5번과 1번 사이 빈 공간이 토출구를 향함
# 1,2,3번은 반시계 방향 / 4,5번은 시계 방향으로 최단 이동
SEASONING_ORDER = ["소금", "설탕", "후추", "고춧가루", "미원"]
SEASONING_POSITION = {
    "소금": {"number": 1, "direction": CLOCKWISE, "steps": 79},
    "설탕": {"number": 2, "direction": CLOCKWISE, "steps": 240},
    "후추": {"number": 3, "direction": CLOCKWISE, "steps": 400},
    "고춧가루": {"number": 4, "direction": COUNTERCLOCKWISE, "steps": 240},
    "미원": {"number": 5, "direction": COUNTERCLOCKWISE, "steps": 79},
}

SAMPLES = 10
DISPLAY_INTERVAL = 0.5

CALIBRATION_FACTOR = 1000
PRESET_MULTIPLIER = 1.0
PRESET_OFFSET_G = 0.0

DEAD_ZONE_G = 0.2
REVERSE_SIGN = True

TARGET_MARGIN_G = 0.3
NEGATIVE_DROP_LIMIT_G = 1.0
TARE_SECONDS = 3

SHUTDOWN_RASPBERRY_PI = False


class HX711:
    def __init__(self, dt_pin, sck_pin):
        self.dt_pin = dt_pin
        self.sck_pin = sck_pin
        self.offset = 0

        GPIO.setup(self.sck_pin, GPIO.OUT)
        GPIO.setup(self.dt_pin, GPIO.IN)
        GPIO.output(self.sck_pin, False)
        time.sleep(0.1)

    def is_ready(self):
        return GPIO.input(self.dt_pin) == 0

    def read_raw(self):
        timeout = time.time() + 1.0

        while not self.is_ready():
            if time.time() > timeout:
                raise TimeoutError("HX711 응답 없음. 배선, 전원, 핀 번호 확인")

        data = 0

        for _ in range(24):
            GPIO.output(self.sck_pin, True)
            data = data << 1
            GPIO.output(self.sck_pin, False)

            if GPIO.input(self.dt_pin):
                data += 1

        GPIO.output(self.sck_pin, True)
        GPIO.output(self.sck_pin, False)

        if data & 0x800000:
            data -= 0x1000000

        return data

    def read_average_raw(self, samples=10):
        values = []

        for _ in range(samples):
            try:
                values.append(self.read_raw())
            except TimeoutError:
                pass
            time.sleep(0.01)

        if not values:
            raise TimeoutError("로드셀 값을 읽지 못했습니다.")

        return statistics.median(values)

    def tare_for_seconds(self, seconds=3):
        print(f"[로드셀] {seconds}초 동안 영점 설정 중... 저울을 비워두세요.")

        values = []
        start = time.time()

        while time.time() - start < seconds:
            try:
                values.append(self.read_raw())
            except TimeoutError:
                pass
            time.sleep(0.03)

        if not values:
            raise TimeoutError("영점 설정 실패: 로드셀 값을 읽지 못했습니다.")

        self.offset = statistics.median(values)
        print(f"[로드셀] 영점 완료. offset = {self.offset:.2f}")

    def get_weight_g(self):
        raw = self.read_average_raw(SAMPLES)
        delta = raw - self.offset

        if REVERSE_SIGN:
            delta = -delta

        weight = delta / CALIBRATION_FACTOR
        weight = weight * PRESET_MULTIPLIER + PRESET_OFFSET_G

        if abs(weight) < DEAD_ZONE_G:
            weight = 0.0

        # 솔레노이드 진동/드리프트로 인한 음수값은 실제 감량이 아니므로 0g 처리
        if weight < 0:
            weight = 0.0

        return weight, raw, delta


def setup_gpio():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    GPIO.setup(BUZZER, GPIO.OUT)

    # active_low 릴레이는 HIGH가 OFF, LOW가 ON
    solenoid_off_before_setup = GPIO.HIGH if SOLENOID_ACTIVE_LOW else GPIO.LOW
    GPIO.setup(SOLENOID_PIN, GPIO.OUT, initial=solenoid_off_before_setup)

    all_led_off()
    solenoid_off()


def all_led_off():
    # LED 미사용: GPIO 충돌 방지를 위해 실제 GPIO 동작 없음
    pass


def led_on_for_seasoning(seasoning):
    # LED 미사용: 조미료 선택 표시는 GUI로만 처리
    pass


def solenoid_on():
    GPIO.output(SOLENOID_PIN, GPIO.LOW if SOLENOID_ACTIVE_LOW else GPIO.HIGH)


def solenoid_off():
    GPIO.output(SOLENOID_PIN, GPIO.HIGH if SOLENOID_ACTIVE_LOW else GPIO.LOW)


_motor_driver = None


def get_motor_driver():
    global _motor_driver

    if RpiMotorLib is None:
        raise RuntimeError("RpiMotorLib가 설치되지 않았습니다. pip install RpiMotorLib 실행 필요")

    if _motor_driver is None:
        _motor_driver = RpiMotorLib.A4988Nema(
            MOTOR_DIRECTION_PIN,
            MOTOR_STEP_PIN,
            MOTOR_MODE_PINS,
            "A4988"
        )

    return _motor_driver


def smooth_motor_go(clockwise, total_steps):
    motor = get_motor_driver()
    motor.motor_go(clockwise, MOTOR_STEPTYPE, total_steps, MOTOR_RUN_DELAY, False, 0)


def move_to_seasoning_position(seasoning):
    info = SEASONING_POSITION.get(seasoning)
    if info is None:
        raise ValueError(f"알 수 없는 조미료 위치: {seasoning}")

    direction_text = "시계" if info["direction"] == CLOCKWISE else "반시계"
    print(f"[모터] {seasoning} {info['number']}번 위치로 이동: {direction_text} 방향 {info['steps']}스텝")
    smooth_motor_go(info["direction"], info["steps"])
    print(f"[모터] {seasoning} 위치 정렬 완료")


def return_to_home_position(seasoning):
    info = SEASONING_POSITION.get(seasoning)
    if info is None:
        return

    reverse_direction = not info["direction"]
    direction_text = "시계" if reverse_direction == CLOCKWISE else "반시계"
    print(f"[모터] 초기 위치 복귀: {direction_text} 방향 {info['steps']}스텝")
    smooth_motor_go(reverse_direction, info["steps"])
    print("[모터] 초기 위치 완료. 5번과 1번 사이 빈 공간이 토출구를 향합니다.")


def play_notes(notes):
    pwm = GPIO.PWM(BUZZER, 440)

    try:
        for freq, duration in notes:
            pwm.ChangeFrequency(freq)
            pwm.start(50)
            time.sleep(duration)
            pwm.stop()
            time.sleep(0.08)

    finally:
        pwm.stop()


def play_wake_buzzer():
    play_notes([(262, 1.0)])
    speak("네 주인님")


def play_complete_buzzer():
    play_notes([
        (262, 0.25),
        (330, 0.25),
        (392, 0.25),
        (523, 0.45),
    ])


def play_shutdown_buzzer():
    play_notes([
        (523, 0.30),
        (392, 0.30),
        (330, 0.30),
        (262, 0.60),
    ])


async def make_tts_file(text):
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
        rate=TTS_RATE,
        volume=TTS_VOLUME,
        pitch=TTS_PITCH
    )
    await communicate.save(TTS_FILE)


def speak(text):
    print(f"[스피커] {text}")

    try:
        asyncio.run(make_tts_file(text))

        result = subprocess.run(
            [
                "mpg123",
                "-q",
                "-f", str(SPEAKER_SCALE),
                "-a", AUDIO_OUTPUT_DEVICE,
                TTS_FILE
            ],
            check=False
        )

        if result.returncode != 0:
            subprocess.run(
                ["mpg123", "-q", "-a", AUDIO_OUTPUT_DEVICE, TTS_FILE],
                check=False
            )

    except Exception as e:
        print(f"[TTS 오류] {e}")
        print("[경고] 음성 출력 실패. 인터넷, edge-tts, mpg123, 스피커 장치 확인 필요.")


def record_audio(filename, seconds):
    if os.path.exists(filename):
        os.remove(filename)

    time.sleep(0.2)
    cmd = [
        "arecord",
        "-D", AUDIO_INPUT_DEVICE,
        "-f", "S16_LE",
        "-r", str(SAMPLE_RATE),
        "-c", "2",
        "-d", str(seconds),
        filename
    ]

    process = subprocess.Popen(cmd)
    process.wait(timeout=seconds + 2)

    if process.returncode != 0:
        raise RuntimeError("arecord 실행 실패")

def speech_to_text(filename):
    recognizer = sr.Recognizer()

    try:
        with sr.AudioFile(filename) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio, language="ko-KR")
        print(f"[음성인식] 인식 결과: {text}")
        return text

    except sr.UnknownValueError:
        print("[음성인식] 알아듣지 못했습니다.")
        return None

    except sr.RequestError as e:
        print(f"[음성인식] 구글 음성인식 요청 실패: {e}")
        return None

    except Exception as e:
        print(f"[음성인식 오류] {e}")
        return None


def is_wake_word(text):
    if text is None:
        return False

    text = text.replace(" ", "")

    wake_words = [
        "헤이조미료",
        "헤이조미료야",
        "해이조미료",
        "헤이조미로",
        "헤이조미뇨",
        "헤이조미료시작",
        "조미료",
        "조미료야",
        "조미로"    ,
        "이조미"    ,
        "이조미료"   ,
        "이조미로"   ,
        "헤이조미"    ,
        "헤이"        ,
        "헤이조"

    ]

    for word in wake_words:
        if word in text:
            return True

    return False


def wait_for_wake_word():
    print("\n[대기 모드] '헤이 조미료'라고 말하면 작동합니다.")

    while True:
        print("[웨이크워드] 듣는 중...")

        try:
            record_audio(WAKE_WAV_FILE, WAKE_RECORD_SECONDS)
            text = speech_to_text(WAKE_WAV_FILE)

            if is_wake_word(text):
                print("[웨이크워드] '헤이 조미료' 감지됨")
                play_wake_buzzer()
                return True

            print("[웨이크워드] 감지 안 됨. 계속 대기합니다.")

        except KeyboardInterrupt:
            raise

        except Exception as e:
            print(f"[웨이크워드 오류] {e}")
            time.sleep(0.5)


def is_shutdown_command(text):
    if text is None:
        return False

    text = text.replace(" ", "")

    shutdown_words = [
        "꺼줘",
        "꺼저",
        "꺼",
        "종료",
        "종료해",
        "종료해줘",
        "시스템종료",
        "시스템꺼줘",
        "그만",
        "그만해",
        "멈춰",
        "멈처",
        "정지",
        "중지",
        "끝",
        "끝내",
        "끝내줘",
        "작동중지"
    ]

    for word in shutdown_words:
        if word in text:
            return True

    return False


def parse_korean_number(text):
    if text is None:
        return None

    text = text.replace(" ", "")

    match = re.search(r"(\d+(\.\d+)?)", text)
    if match:
        return float(match.group(1))

    simple_nums = {
        "영": 0, "공": 0,
        "한": 1, "하나": 1, "일": 1,
        "두": 2, "둘": 2, "이": 2,
        "세": 3, "셋": 3, "삼": 3,
        "네": 4, "넷": 4, "사": 4,
        "다섯": 5, "오": 5,
        "여섯": 6, "육": 6,
        "일곱": 7, "칠": 7,
        "여덟": 8, "팔": 8,
        "아홉": 9, "구": 9,
        "열": 10, "십": 10,
        "스물": 20, "스무": 20, "이십": 20,
        "서른": 30, "삼십": 30,
        "마흔": 40, "사십": 40,
        "쉰": 50, "오십": 50,
    }

    for word in sorted(simple_nums.keys(), key=len, reverse=True):
        if word in text:
            return float(simple_nums[word])

    return None


def normalize_text(text):
    if text is None:
        return None

    text = text.replace(" ", "")
    text = text.replace("그램", "g")
    text = text.replace("그람", "g")
    text = text.replace("쥐", "g")
    text = text.replace("지", "g")
    return text


def parse_command(text):
    if text is None:
        return None, None

    raw_text = text
    text = normalize_text(text)

    seasoning = None

    if "소금" in text:
        seasoning = "소금"
    elif "설탕" in text:
        seasoning = "설탕"
    elif "후추" in text:
        seasoning = "후추"
    elif "고춧가루" in text or "고추가루" in text or "고춧" in text or "고추" in text:
        seasoning = "고춧가루"
    elif "미원" in text or "조미료" in text and "미원" in raw_text:
        seasoning = "미원"

    gram = parse_korean_number(raw_text)

    return seasoning, gram


def apply_loadcell_filter(weight, prev_weight):
    # 조미료 토출 중 무게는 감소하지 않는 것이 정상이다.
    # 이전 측정값보다 1g 이상 급락하면 솔레노이드/진동 노이즈로 보고 이전값 유지.
    if weight - prev_weight < -NEGATIVE_DROP_LIMIT_G:
        print(f"[FILTER] 급격한 음수 튐 감지: {prev_weight:.2f}g -> {weight:.2f}g, 이전값 유지")
        return prev_weight

    return weight


def wait_until_target_weight(hx, target_g):
    print("[로드셀] 목표 무게까지 측정 시작")
    print("--------------------------------")

    solenoid_off()

    prev_weight = 0.0

    try:
        while True:
            weight, raw, delta = hx.get_weight_g()
            weight = apply_loadcell_filter(weight, prev_weight)

            if weight > prev_weight:
                prev_weight = weight

            print(f"현재 무게: {weight:6.2f} g / 목표: {target_g:.2f} g")

            if weight >= target_g - TARGET_MARGIN_G:
                print("[로드셀] 목표 무게 도달")
                break

            print("[솔레노이드] ON")
            solenoid_on()
            time.sleep(SOLENOID_ON_SECONDS)

            print("[솔레노이드] OFF")
            solenoid_off()

            time.sleep(1.0)

            weight, raw, delta = hx.get_weight_g()
            weight = apply_loadcell_filter(weight, prev_weight)

            if weight > prev_weight:
                prev_weight = weight

            print(f"현재 무게: {weight:6.2f} g / 목표: {target_g:.2f} g")

            if weight >= target_g - TARGET_MARGIN_G:
                print("[로드셀] 목표 무게 도달")
                break

            time.sleep(SOLENOID_OFF_SECONDS)

    finally:
        solenoid_off()


def handle_command_once(hx):
    print("[명령 입력] 3초 동안 조미료와 그람수를 말하세요.")
    print("예시: 소금 5그램 / 설탕 10그램 / 후추 3그램 / 꺼줘")

    record_audio(COMMAND_WAV_FILE, COMMAND_RECORD_SECONDS)
    text = speech_to_text(COMMAND_WAV_FILE)

    if text is None:
        print("[명령 입력] 아무 말도 인식하지 못했습니다. 다시 대기 모드로 돌아갑니다.")
        return "continue"

    if is_shutdown_command(text):
        print("[시스템] 종료 명령을 인식했습니다.")
        all_led_off()
        speak("시스템을 종료합니다.")
        play_shutdown_buzzer()

        if SHUTDOWN_RASPBERRY_PI:
            subprocess.run(["sudo", "shutdown", "now"], check=False)

        return "shutdown"

    seasoning, target_g = parse_command(text)

    if seasoning is None or target_g is None:
        print("[명령 오류] 조미료 또는 그람수를 인식하지 못했습니다.")
        speak("다시 호출해 주세요. 예를 들어, 헤이 조미료라고 말한 뒤 소금 5그램처럼 말해주세요.")
        return "continue"

    print("\n===== 입력 명령 =====")
    print(f"원본 인식: {text}")
    print(f"조미료: {seasoning}")
    print(f"목표 무게: {target_g:.2f} g")
    print("====================")

    positioned = False

    try:
        led_on_for_seasoning(seasoning)

        speak(f"{seasoning} {target_g:g}그램 출력을 위한 위치로 이동합니다.")

        solenoid_on()
        time.sleep(1.0)

        move_to_seasoning_position(seasoning)
        positioned = True

        time.sleep(1.0) 
        solenoid_off()
        time.sleep(1.5)

        speak(f"{seasoning} {target_g:g}그램, 조미료 출력을 시작합니다.")

        hx.tare_for_seconds(TARE_SECONDS)

        wait_until_target_weight(hx, target_g)

        play_complete_buzzer()

        speak("조미료 출력이 완료되었습니다. 초기 위치로 복귀합니다.")

    except Exception as e:
        print(f"[동작 오류] {e}")
        speak("동작 중 오류가 발생했습니다. 장치 상태를 확인해 주세요.")

    finally:
        if positioned:
            try:
                solenoid_on()
                time.sleep(1.0)
                return_to_home_position(seasoning)
                time.sleep(1.0)
            except Exception as e:
                print(f"[모터 복귀 오류] {e}")

        solenoid_off()
        all_led_off()
    return "continue"


def cleanup_temp_files():
    for file in [WAKE_WAV_FILE, COMMAND_WAV_FILE, TTS_FILE]:
        if os.path.exists(file):
            os.remove(file)

def run_led_select_mode(seasoning):
    try:
        setup_gpio()
        solenoid_off()
    except Exception as e:
        print(f"[터치 모드] 솔레노이드 OFF 실패: {e}")

    print(f"[터치 모드] LED 미사용: {seasoning}")

def run_led_off_mode():
    try:
        setup_gpio()
        solenoid_off()
    except Exception as e:
        print(f"[터치 모드] 솔레노이드 OFF 실패: {e}")

    print("[터치 모드] LED OFF 요청 무시: LED 미사용")

def run_manual_mode(seasoning, target_g):
    if seasoning not in SEASONING_ORDER:
        print(f"[수동 모드 오류] 알 수 없는 조미료: {seasoning}")
        return

    if target_g <= 0:
        print("[수동 모드 오류] 목표 무게는 0g보다 커야 합니다.")
        return

    setup_gpio()
    hx = HX711(DT_PIN, SCK_PIN)

    print("================================")
    print("스마트 조미료 디스펜서 수동 터치 모드 시작")
    print("===== 입력 명령 =====")
    print("원본 인식: 터치 모드")
    print(f"조미료: {seasoning}")
    print(f"목표 무게: {target_g:.2f} g")
    print("====================")

    positioned = False

    try:
        led_on_for_seasoning(seasoning)

        speak(f"{seasoning} {target_g:g}그램 출력을 위한 위치로 이동합니다.")

        solenoid_on()
        time.sleep(1.0)

        move_to_seasoning_position(seasoning)
        positioned = True

        time.sleep(1.0)
        solenoid_off()
        time.sleep(1.5)

        speak(f"{seasoning} {target_g:g}그램, 조미료 출력을 시작합니다.")

        hx.tare_for_seconds(TARE_SECONDS)

        wait_until_target_weight(hx, target_g)

        play_complete_buzzer()

        speak("수동 출력을 종료하고 초기 위치로 복귀합니다.")

    except KeyboardInterrupt:
        print("\n[시스템] 수동 출력을 강제 종료합니다.")

    except Exception as e:
        print(f"[수동 모드 오류] {e}")

    finally:
        if positioned:
            try:
                solenoid_on()
                time.sleep(1.0)
                return_to_home_position(seasoning)
                time.sleep(1.0)
            except Exception as e:
                print(f"[모터 복귀 오류] {e}")

        solenoid_off()
        all_led_off()
        GPIO.cleanup()
        cleanup_temp_files()

def main(skip_start_voice=False):
    setup_gpio()
    hx = HX711(DT_PIN, SCK_PIN)

    print("================================")
    print("스마트 조미료 디스펜서 시스템 시작")
    print("대기어: 헤이 조미료")
    print("명령 예시: 소금 5그램, 설탕 10그램, 후추 3그램, 고춧가루 2그램, 미원 1그램")
    print("종료 명령: 꺼줘, 종료해줘, 그만, 멈춰")
    print("Ctrl + C 로 강제 종료")
    print("================================")

    try:
        if not skip_start_voice:
            speak("스마트 조미료 디스펜서 출력을 시작합니다. 헤이 조미료라고 불러주세요.")

        while True:
            all_led_off()

            wait_for_wake_word()

            result = handle_command_once(hx)

            if result == "shutdown":
                break

    except KeyboardInterrupt:
        print("\n[시스템] 강제 종료합니다.")
        all_led_off()

    finally:
        solenoid_off()
        all_led_off()
        GPIO.cleanup()
        cleanup_temp_files()


def cli_entry():
    parser = argparse.ArgumentParser()

    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--seasoning", type=str)
    parser.add_argument("--grams", type=float)

    parser.add_argument("--led", type=str)
    parser.add_argument("--led-off", action="store_true")
    parser.add_argument("--no-start-voice", action="store_true")

    args = parser.parse_args()

    if args.led_off:
        run_led_off_mode()
        return

    if args.led:
        run_led_select_mode(args.led)
        return

    if args.manual:
        if args.seasoning is None or args.grams is None:
            print("[수동 모드 오류] —seasoning 과 —grams 값이 필요합니다.")
            return

        run_manual_mode(args.seasoning, args.grams)
        return

    main(skip_start_voice=args.no_start_voice)


if __name__ == "__main__":
    cli_entry()