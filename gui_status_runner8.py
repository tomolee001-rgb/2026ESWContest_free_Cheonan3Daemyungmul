import tkinter as tk
import subprocess
import threading
import queue
import re
import os
import sys
import signal
import time

from PIL import Image, ImageTk


TARGET_SCRIPT = "demo8.py"

IMAGE_FILES = {
    "소금": "salt.png",
    "설탕": "sugar.png",
    "후추": "pepper.png",
    "고춧가루": "chili.png",
    "미원": "miwon.png"
}

WIDTH = 800
HEIGHT = 450

DISPLAY_X = 800
DISPLAY_Y = 0

BG = "#242628"
TOP = "#111418"
CARD = "#3b4152"
CARD2 = "#474f63"
STATUS_BG = "#2c303b"
TEXT = "#f2f2f2"
SUBTEXT = "#cfd3dc"

RED = "#ff5a5a"
GREEN = "#38d27a"
YELLOW = "#f3d24f"
BLUE = "#63a6ff"
GRAY = "#8b93a7"
ORANGE = "#ffb347"


class StatusGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SMART AI SEASONING STATUS")
        self.root.overrideredirect(True)

        self.root.geometry(f"{WIDTH}x{HEIGHT}+{DISPLAY_X}+{DISPLAY_Y}")
        self.root.update_idletasks()
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{DISPLAY_X}+{DISPLAY_Y}")

        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", True)

        self.process = None
        self.process_mode = "voice"
        self.log_queue = queue.Queue()

        self.skip_next_start_voice = False

        self.current_weight = 0.0
        self.target_weight = 0.0
        self.seasoning = "-"
        self.command_text = "-"
        self.state = "ready"

        self.status_text_value = "[SYSTEM READY] 헤이 조미료 대기 중..."

        self.image_cache = {}
        self.current_spice_photo = None

        self.touch_frame = None
        self.touch_selected = None
        self.touch_target_weight = 0.0
        self.touch_spice_buttons = {}

        self.touch_status_label = None
        self.touch_current_label = None
        self.touch_target_label = None
        self.touch_selected_label = None
        self.touch_weight_label = None

        self.load_images()
        self.build_ui()

        self.root.after(100, self.process_log_queue)
        self.root.after(500, self.start_system)

    def load_images(self):
        for key, path in IMAGE_FILES.items():
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((95, 95), Image.LANCZOS)
                    self.image_cache[key] = ImageTk.PhotoImage(img)
                except Exception as e:
                    print(f"[GUI] 이미지 로드 실패: {path} / {e}")
                    self.image_cache[key] = None
            else:
                print(f"[GUI] 이미지 파일 없음: {path}")
                self.image_cache[key] = None

    def build_ui(self):
        self.canvas = tk.Canvas(
            self.root,
            width=WIDTH,
            height=HEIGHT,
            bg=BG,
            highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.create_rectangle(20, 10, 780, 60, fill=TOP, outline="")
        self.canvas.create_text(
            40, 35,
            text="SMART AI SEASONING",
            anchor="w",
            font=("Helvetica", 19, "bold"),
            fill=TEXT
        )

        self.canvas.create_rectangle(20, 75, 460, 250, fill=CARD, outline="")
        self.canvas.create_text(
            240, 105,
            text="CURRENT WEIGHT",
            font=("Helvetica", 15, "bold"),
            fill=SUBTEXT
        )

        self.weight_text = self.canvas.create_text(
            240, 160,
            text="0.0",
            font=("Helvetica", 44, "bold"),
            fill=TEXT
        )

        self.canvas.create_text(
            240, 210,
            text="GRAMS",
            font=("Helvetica", 17, "bold"),
            fill=SUBTEXT
        )

        self.canvas.create_rectangle(60, 230, 420, 243, fill="#2d3342", outline="")
        self.progress_rect = self.canvas.create_rectangle(
            60, 230, 60, 243,
            fill=GREEN,
            outline=""
        )

        self.canvas.create_rectangle(490, 75, 780, 310, fill=CARD2, outline="")
        self.canvas.create_text(
            635, 100,
            text="SELECTED SEASONING",
            font=("Helvetica", 15, "bold"),
            fill=SUBTEXT
        )

        self.spice_image_label = tk.Label(
            self.root,
            bg=CARD2,
            bd=0,
            highlightthickness=0
        )
        self.spice_image_label.place(x=565, y=115, width=140, height=130)

        self.spice_name_text = self.canvas.create_text(
            635, 275,
            text="-",
            font=("Helvetica", 22, "bold"),
            fill=TEXT
        )

        self.canvas.create_rectangle(20, 265, 460, 350, fill=STATUS_BG, outline="")
        self.status_text = self.canvas.create_text(
            45, 307,
            text=self.status_text_value,
            anchor="w",
            font=("Helvetica", 15, "bold"),
            fill=BLUE
        )

        self.command_label = self.canvas.create_text(
            30, 385,
            text="COMMAND: -",
            anchor="w",
            font=("Helvetica", 13, "bold"),
            fill=SUBTEXT
        )

        self.target_label = self.canvas.create_text(
            520, 385,
            text="TARGET: 0.0 g",
            anchor="w",
            font=("Helvetica", 13, "bold"),
            fill=SUBTEXT
        )

        self.btn_touch = tk.Button(
            self.root,
            text="Touch Mode",
            command=self.open_touch_mode,
            font=("Helvetica", 10, "bold"),
            bg="#3f8cff",
            fg="white",
            activebackground="#69a7ff",
            relief="flat"
        )
        self.btn_touch.place(x=555, y=410, width=110, height=28)

        self.btn_close = tk.Button(
            self.root,
            text="Device Off",
            command=self.close_gui,
            font=("Helvetica", 10, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        self.btn_close.place(x=680, y=410, width=90, height=28)

        self.root.bind("<Escape>", lambda e: self.close_gui())

    def start_system(self):
        if self.process is not None and self.process.poll() is None:
            return

        if not os.path.exists(TARGET_SCRIPT):
            self.set_status(f"[ERROR] {TARGET_SCRIPT} 파일을 찾을 수 없습니다.", "error")
            return

        self.set_status("[SYSTEM START] 기존 시스템 코드 실행 중...", "processing")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [sys.executable, "-u", TARGET_SCRIPT]

        if getattr(self, "skip_next_start_voice", False):
            cmd.append("--no-start-voice")
            self.skip_next_start_voice = False

        self.process_mode = "voice"

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=os.getcwd(),
            preexec_fn=os.setsid
        )

        thread = threading.Thread(
            target=self.read_process_output,
            args=("voice",),
            daemon=True
        )
        thread.start()

    def read_process_output(self, mode="voice"):
        try:
            for line in self.process.stdout:
                line = line.strip()
                if line:
                    self.log_queue.put(line)
        except Exception as e:
            self.log_queue.put(f"[GUI ERROR] 출력 읽기 실패: {e}")

        self.log_queue.put(f"[GUI_PROCESS_END:{mode}]")

    def process_log_queue(self):
        while not self.log_queue.empty():
            line = self.log_queue.get()
            print(line)
            self.parse_line(line)

        self.root.after(100, self.process_log_queue)

    def parse_line(self, line):
        if line.startswith("[GUI_PROCESS_END:"):
            mode = line.replace("[GUI_PROCESS_END:", "").replace("]", "")

            if mode == "manual":
                self.set_status("[DONE] 수동 출력을 종료합니다", "done")
                None  # LED 미사용
                self.root.after(2000, self.return_to_voice_mode)
                return

            if mode == "voice":
                if self.touch_frame is None:
                    self.set_status("[OFF] 시스템 코드 종료됨", "shutdown")
                return

        if "[대기 모드]" in line or "[웨이크워드] 듣는 중" in line:
            self.set_status("[SYSTEM READY] 헤이 조미료 대기 중...", "ready")
            self.set_command("-")
            return

        if "헤이 조미료" in line and "감지" in line:
            self.set_status("[WAKE] 호출 감지, 명령 대기 중...", "listening")
            return

        if "[명령 입력]" in line:
            self.set_status("[LISTENING] 조미료와 그람수를 듣는 중...", "listening")
            return

        if "[음성인식] 인식 결과:" in line:
            text = line.split(":", 1)[-1].strip()
            self.set_command(text)
            self.set_status("[STT] 음성 인식 완료", "processing")
            return

        if "알아듣지 못했습니다" in line or "인식하지 못했습니다" in line:
            self.set_status("[RETRY] 음성을 인식하지 못했습니다", "error")
            return

        if "원본 인식:" in line:
            text = line.split(":", 1)[-1].strip()
            self.set_command(text)
            return

        if line.startswith("조미료:"):
            seasoning = line.split(":", 1)[-1].strip()
            self.set_seasoning(seasoning)
            return

        if line.startswith("목표 무게:"):
            match = re.search(r"([0-9]+(\.[0-9]+)?)", line)
            if match:
                self.target_weight = float(match.group(1))
                self.update_weight_display()
            return

        if "[스피커]" in line:
            msg = line.replace("[스피커]", "").strip()

            if "출력을 시작" in msg:
                self.set_status("[DISPENSING] 조미료 출력을 시작합니다", "dispensing")
            elif "수동 출력" in msg:
                self.set_status("[DONE] 수동 출력을 종료합니다", "done")
            elif "완료" in msg:
                self.set_status("[DONE] 조미료 출력 완료", "done")
            elif "종료" in msg:
                self.set_status("[SHUTDOWN] 시스템 종료 중...", "shutdown")
            else:
                self.set_status(f"[VOICE] {msg}", "processing")
            return

        if "영점 설정 중" in line:
            self.set_status("[TARE] 로드셀 영점 설정 중...", "tare")
            self.current_weight = 0.0
            self.update_weight_display()
            return

        if "영점 완료" in line:
            self.set_status("[TARE COMPLETE] 영점 설정 완료", "tare")
            return

        if "[모터]" in line and "이동" in line:
            self.set_status("[POSITIONING] 조미료통 위치 정렬 중...", "processing")
            return

        if "[모터]" in line and "초기 위치" in line:
            self.set_status("[HOMING] 초기 위치로 복귀 중...", "processing")
            return

        if "[솔레노이드] ON" in line:
            self.set_status("[DISPENSING] 솔레노이드 작동 중...", "dispensing")
            return

        if "목표 무게까지 측정 시작" in line:
            self.set_status("[MEASURING] 목표 무게까지 측정 중...", "dispensing")
            return

        if "현재 무게:" in line and "목표:" in line:
            match = re.search(
                r"현재 무게:\s*([-0-9.]+)\s*g\s*/\s*목표:\s*([-0-9.]+)\s*g",
                line
            )
            if match:
                self.current_weight = float(match.group(1))
                self.target_weight = float(match.group(2))
                self.set_status("[MEASURING] 조미료 출력 중...", "dispensing")
                self.update_weight_display()
            return

        if "목표 무게 도달" in line:
            self.current_weight = self.target_weight
            self.update_weight_display()
            self.set_status("[DONE] 목표 무게 도달", "done")
            return

        if "종료 명령" in line or "강제 종료" in line:
            self.set_status("[SHUTDOWN] 시스템 종료 중...", "shutdown")
            return

    def set_status(self, text, state="ready"):
        self.status_text_value = text
        self.state = state

        color = TEXT
        if state == "ready":
            color = BLUE
        elif state == "listening":
            color = GREEN
        elif state == "processing":
            color = ORANGE
        elif state == "tare":
            color = YELLOW
        elif state == "dispensing":
            color = ORANGE
        elif state == "done":
            color = GREEN
        elif state == "shutdown":
            color = GRAY
        elif state == "error":
            color = RED

        self.canvas.itemconfig(self.status_text, text=text, fill=color)

        if self.touch_status_label is not None and self.touch_status_label.winfo_exists():
            self.touch_status_label.config(text=text, fg=color)

    def set_command(self, text):
        self.command_text = text
        self.canvas.itemconfig(self.command_label, text=f"COMMAND: {text}")

        if "소금" in text:
            self.set_seasoning("소금")
        elif "설탕" in text:
            self.set_seasoning("설탕")
        elif "후추" in text:
            self.set_seasoning("후추")
        elif "고춧가루" in text or "고추가루" in text or "고추" in text:
            self.set_seasoning("고춧가루")
        elif "미원" in text:
            self.set_seasoning("미원")

    def set_seasoning(self, seasoning):
        self.seasoning = seasoning

        if seasoning == "소금":
            color = RED
        elif seasoning == "설탕":
            color = GREEN
        elif seasoning == "후추":
            color = YELLOW
        elif seasoning == "고춧가루":
            color = ORANGE
        elif seasoning == "미원":
            color = BLUE
        else:
            color = SUBTEXT

        self.canvas.itemconfig(self.spice_name_text, text=seasoning, fill=color)
        self.update_spice_image(seasoning)
        self.update_weight_display()

    def update_spice_image(self, seasoning):
        photo = self.image_cache.get(seasoning)

        if photo is not None:
            self.spice_image_label.config(image=photo, text="")
            self.spice_image_label.image = photo
        else:
            self.spice_image_label.config(
                image="",
                text="이미지 없음",
                fg="white",
                bg=CARD2,
                font=("Helvetica", 15, "bold")
            )
            self.spice_image_label.image = None

    def update_weight_display(self):
        self.canvas.itemconfig(self.weight_text, text=f"{self.current_weight:.1f}")
        self.canvas.itemconfig(self.target_label, text=f"TARGET: {self.target_weight:.1f} g")

        if self.target_weight <= 0:
            progress = 0.0
        else:
            progress = max(0.0, min(self.current_weight / self.target_weight, 1.0))

        x2 = 60 + int(360 * progress)

        if self.seasoning == "소금":
            color = RED
        elif self.seasoning == "설탕":
            color = GREEN
        elif self.seasoning == "후추":
            color = YELLOW
        elif self.seasoning == "고춧가루":
            color = ORANGE
        elif self.seasoning == "미원":
            color = BLUE
        else:
            color = GREEN

        self.canvas.coords(self.progress_rect, 60, 230, max(x2, 60), 243)
        self.canvas.itemconfig(self.progress_rect, fill=color)

        if self.touch_current_label is not None and self.touch_current_label.winfo_exists():
            self.touch_current_label.config(text=f"현재 무게: {self.current_weight:.1f} g")

        if self.touch_target_label is not None and self.touch_target_label.winfo_exists():
            self.touch_target_label.config(text=f"목표 무게: {self.target_weight:.1f} g")

    def stop_system_process(self):
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                except Exception:
                    self.process.send_signal(signal.SIGINT)

                time.sleep(0.5)

                if self.process.poll() is None:
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    except Exception:
                        self.process.terminate()

                time.sleep(0.3)

        except Exception as e:
            print(f"[GUI] 프로세스 종료 중 오류: {e}")

        self.process = None

    def open_touch_mode(self):
        self.stop_system_process()

        self.touch_selected = None
        self.touch_target_weight = 0.0

        self.current_weight = 0.0
        self.target_weight = 0.0
        self.update_weight_display()

        self.set_command("Touch Mode")
        self.set_status("[TOUCH MODE] 조미료를 선택하세요", "listening")

        self.build_touch_ui()

    def build_touch_ui(self):
        if self.touch_frame is not None:
            self.touch_frame.destroy()

        self.touch_frame = tk.Frame(self.root, bg=BG, width=WIDTH, height=HEIGHT)
        self.touch_frame.place(x=0, y=0)

        tk.Label(
            self.touch_frame,
            text="SMART AI SEASONING - TOUCH MODE",
            font=("Helvetica", 18, "bold"),
            bg=TOP,
            fg=TEXT
        ).place(x=20, y=8, width=760, height=42)

        self.touch_status_label = tk.Label(
            self.touch_frame,
            text="조미료를 선택하세요",
            font=("Helvetica", 13, "bold"),
            bg=STATUS_BG,
            fg=BLUE,
            anchor="w",
            padx=15
        )
        self.touch_status_label.place(x=20, y=58, width=760, height=35)

        spices = ["소금", "설탕", "후추", "고춧가루", "미원"]
        x_positions = [20, 175, 330, 485, 640]

        self.touch_spice_buttons = {}

        for spice, x in zip(spices, x_positions):
            card = tk.Frame(
                self.touch_frame,
                bg=CARD2,
                width=140,
                height=165,
                highlightthickness=2,
                highlightbackground="#5d657d"
            )
            card.place(x=x, y=105)

            photo = self.image_cache.get(spice)

            btn = tk.Button(
                card,
                text=spice,
                image=photo if photo is not None else "",
                compound="top",
                command=lambda s=spice: self.touch_select_seasoning(s),
                font=("Helvetica", 15, "bold"),
                bg=CARD2,
                fg=TEXT,
                activebackground="#5d657d",
                activeforeground="white",
                relief="flat"
            )
            btn.image = photo
            btn.place(x=8, y=8, width=124, height=148)

            self.touch_spice_buttons[spice] = btn


        btn_back = tk.Button(
            self.touch_frame,
            text="Voice Mode",
            command=self.return_to_voice_mode,
            font=("Helvetica", 10, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        btn_back.place(x=30, y=330, width=110, height=40)

        btn_minus_1g = tk.Button(
            self.touch_frame,
            text="-1g",
            command=lambda: self.touch_change_weight(-1.0),
            font=("Helvetica", 12, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        btn_minus_1g.place(x=155, y=330, width=55, height=40)

        btn_minus = tk.Button(
            self.touch_frame,
            text="-",
            command=lambda: self.touch_change_weight(-0.1),
            font=("Helvetica", 21, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        btn_minus.place(x=215, y=330, width=55, height=40)

        self.touch_weight_label = tk.Label(
            self.touch_frame,
            text="0.0 g",
            font=("Helvetica", 21, "bold"),
            bg=BG,
            fg=TEXT
        )
        self.touch_weight_label.place(x=280, y=330, width=160, height=40)

        btn_plus = tk.Button(
            self.touch_frame,
            text="+",
            command=lambda: self.touch_change_weight(0.1),
            font=("Helvetica", 21, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        btn_plus.place(x=450, y=330, width=55, height=40)

        btn_plus_1g = tk.Button(
            self.touch_frame,
            text="+1g",
            command=lambda: self.touch_change_weight(1.0),
            font=("Helvetica", 12, "bold"),
            bg="#5d657d",
            fg="white",
            activebackground="#737c99",
            relief="flat"
        )
        btn_plus_1g.place(x=510, y=330, width=55, height=40)

        btn_confirm = tk.Button(
            self.touch_frame,
            text="Start",
            command=self.touch_confirm_weight,
            font=("Helvetica", 14, "bold"),
            bg=GREEN,
            fg="white",
            activebackground="#5ee096",
            relief="flat"
        )
        btn_confirm.place(x=585, y=330, width=135, height=40)

    def touch_select_seasoning(self, seasoning):
        self.touch_selected = seasoning
        self.set_seasoning(seasoning)

        if self.touch_selected_label is not None and self.touch_selected_label.winfo_exists():
            self.touch_selected_label.config(text=f"선택 조미료: {seasoning}", fg=TEXT)

        for spice, btn in self.touch_spice_buttons.items():
            if spice == seasoning:
                btn.config(bg="#3f8cff", activebackground="#69a7ff")
            else:
                btn.config(bg=CARD2, activebackground="#5d657d")

        None  # LED 미사용
        self.update_touch_status(f"{seasoning} 선택됨. 목표 무게를 설정하세요.")

    def touch_change_weight(self, delta):
        self.touch_target_weight = round(self.touch_target_weight + delta, 1)

        if self.touch_target_weight < 0:
            self.touch_target_weight = 0.0

        self.target_weight = self.touch_target_weight
        self.update_weight_display()

        if self.touch_weight_label is not None and self.touch_weight_label.winfo_exists():
            self.touch_weight_label.config(text=f"{self.touch_target_weight:.1f} g")

    def touch_confirm_weight(self):
        if self.touch_selected is None:
            self.update_touch_status("먼저 소금 / 설탕 / 후추 / 고춧가루 / 미원 중 하나를 선택하세요.")
            return

        if self.touch_target_weight <= 0:
            self.update_touch_status("목표 무게는 0.1g 이상으로 설정하세요.")
            return

        self.start_manual_process(self.touch_selected, self.touch_target_weight)

    def start_manual_process(self, seasoning, grams):
        if self.process is not None and self.process.poll() is None:
            return

        self.destroy_touch_ui()

        self.current_weight = 0.0
        self.target_weight = grams
        self.set_seasoning(seasoning)
        self.update_weight_display()

        self.set_command(f"Touch Mode: {seasoning} {grams:.1f}g")
        self.set_status("[DISPENSING] 조미료 출력을 시작합니다", "dispensing")

        cmd = [
            sys.executable,
            "-u",
            TARGET_SCRIPT,
            "--manual",
            "--seasoning",
            seasoning,
            "--grams",
            str(grams)
        ]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        self.process_mode = "manual"

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=os.getcwd(),
            preexec_fn=os.setsid
        )

        thread = threading.Thread(
            target=self.read_process_output,
            args=("manual",),
            daemon=True
        )
        thread.start()

    def update_touch_status(self, text):
        if self.touch_status_label is not None and self.touch_status_label.winfo_exists():
            self.touch_status_label.config(text=text)

    def destroy_touch_ui(self):
        if self.touch_frame is not None:
            self.touch_frame.destroy()
            self.touch_frame = None

        self.touch_status_label = None
        self.touch_current_label = None
        self.touch_target_label = None
        self.touch_selected_label = None
        self.touch_weight_label = None
        self.touch_spice_buttons = {}

    def return_to_voice_mode(self):
        self.stop_system_process()
        None  # LED 미사용

        self.destroy_touch_ui()

        self.touch_selected = None
        self.touch_target_weight = 0.0

        self.current_weight = 0.0
        self.target_weight = 0.0
        self.seasoning = "-"
        self.command_text = "-"

        self.set_command("-")
        self.set_seasoning("-")
        self.update_weight_display()

        self.skip_next_start_voice = True

        self.set_status("[SYSTEM READY] 음성 인식 모드로 복귀 중...", "processing")
        self.root.after(500, self.start_system)

    def run_backend_led(self, seasoning):
        # LED 미사용: GPIO 충돌 방지를 위해 백엔드 LED 호출 제거
        return

    def run_backend_led_off(self):
        # LED 미사용: GPIO 충돌 방지를 위해 백엔드 LED OFF 호출 제거
        return

    def close_gui(self):
        try:
            self.stop_system_process()
            None  # LED 미사용
        except Exception:
            pass

        self.root.destroy()


def main():
    root = tk.Tk()
    app = StatusGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
