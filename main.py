import socket
import threading
import sys
import time
import platform
import subprocess
import traceback
import os
import datetime
import json
import base64
import hmac
import hashlib
import secrets
import getpass
from collections import deque

VERSION = "v0.4.3_Final"
LISTEN_PORT = 11451
ESP32_PORT = 11452
RADMIN_PORT = 11452
TARGET_IP = "127.0.0.1"
ESP32_IP = None

DATA_DIR = "NX-HLB_Data"
PROFILES_FILE = os.path.join(DATA_DIR, "wifi_profiles.enc")
AUTH_FILE = os.path.join(DATA_DIR, "auth.dat")
KEY_FILE = os.path.join(DATA_DIR, "esp_key.enc")
LAST_VIEW = ""

DERIVED_KEY = None
ESP32_SECRET = None
MASTER_ENC_KEY = None
MASTER_MAC_KEY = None

RECENT_NONCES = deque(maxlen=1000)
QUEUE_LOCK = threading.Lock()
TERMINAL_QUEUE = []

IS_FROZEN = getattr(sys, 'frozen', False)
sys_status = {
    "radmin": "未检测到运行", "hardware": "暂时无法连接至硬件", "service_running": False,
    "running": True, "fatal_error": False, "fatal_msg": "", "action_msg": "系统启动，正在初始化...",
    "view": "main", "need_redraw": False, "detect_target": None, "progress": 0,
    "selected_preset": None, "show_password": False
}

# ================= 安全加密与密码 =================
PBKDF2_ITER = 100000

def get_password_hash(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITER)
    return salt.hex() + key.hex()

def verify_password(stored_hash, provided_password):
    try:
        salt = bytes.fromhex(stored_hash[:32])
        expected_key = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, PBKDF2_ITER).hex()
        return hmac.compare_digest(stored_hash[32:], expected_key)
    except:
        return False

def init_master_keys(derived_key_bytes):
    global MASTER_ENC_KEY, MASTER_MAC_KEY
    master = hashlib.pbkdf2_hmac('sha256', derived_key_bytes, b"NXHLB_SALT_2026", 10000, dklen=64)
    MASTER_ENC_KEY = master[:32]
    MASTER_MAC_KEY = master[32:]

def encrypt_data(data_str):
    if not MASTER_ENC_KEY or not MASTER_MAC_KEY: return ""
    nonce = os.urandom(16)
    b = data_str.encode('utf-8')
    ct = bytes(b[i] ^ MASTER_ENC_KEY[i % 32] for i in range(len(b)))
    tag = hmac.new(MASTER_MAC_KEY, nonce + ct, hashlib.sha256).digest()[:16]
    return base64.b64encode(nonce + tag + ct).decode('utf-8')

def decrypt_data(enc_str):
    if not MASTER_ENC_KEY or not MASTER_MAC_KEY: return None
    try:
        raw = base64.b64decode(enc_str)
        if len(raw) >= 48:
            nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
            expected = hmac.new(MASTER_MAC_KEY, nonce + ct, hashlib.sha256).digest()[:16]
            if hmac.compare_digest(expected, tag):
                pt = bytes(ct[i] ^ MASTER_ENC_KEY[i % 32] for i in range(len(ct)))
                return pt.decode('utf-8')
    except: pass
    return None

# ================= 支持中文的输入模块 =================
def read_utf8_char():
    """跨平台读取一个完整的 UTF-8 字符（支持中文）"""
    if platform.system() == "Windows":
        import msvcrt
        ch = msvcrt.getch()
        if ch == b'\x03': sys.exit(0)
        if ch in [b'\r', b'\n']: return '\n'
        if ch == b'\x08': return '\b'
        if ch == b'\x1b': return '\x1b'
        
        # 处理中文/多字节 UTF-8
        buffer = bytearray(ch)
        while True:
            try:
                return buffer.decode('utf-8')
            except UnicodeDecodeError:
                if len(buffer) > 4: return '' # 非法字节
                buffer.extend(msvcrt.getch())
    else:
        return sys.stdin.read(1)

def get_masked_input(prompt):
    print(prompt, end="", flush=True)
    result = ""
    while True:
        char = read_utf8_char()
        if char == '\n':
            print()
            return result
        elif char == '\b':
            if len(result) > 0:
                result = result[:-1]
                sys.stdout.write('\b \b'); sys.stdout.flush()
        elif char not in ['\x1b', '\x03']:
            if char.isprintable():
                result += char
                sys.stdout.write('*'); sys.stdout.flush()

def get_user_input(prompt=""):
    print(prompt, end="", flush=True)
    result = ""
    while True:
        char = read_utf8_char()
        if char == '\x1b': return None
        elif char == '\n': return result
        elif char == '\b':
            if len(result) > 0:
                result = result[:-1]
                sys.stdout.write('\b \b'); sys.stdout.flush()
        elif char not in ['\x03']:
            if char.isprintable():
                result += char
                sys.stdout.write(char); sys.stdout.flush()

def init_security():
    global DERIVED_KEY, ESP32_SECRET
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(AUTH_FILE):
        print("\033[96m[首次启动] 请设置全局安全密码（用于保护本地数据和通信密钥）\033[0m")
        while True:
            pwd1 = get_masked_input(" 设置密码: ")
            if not pwd1: continue
            pwd2 = get_masked_input(" 确认密码: ")
            if pwd1 == pwd2:
                h = get_password_hash(pwd1)
                with open(AUTH_FILE, "w", encoding="utf-8") as f: f.write(h)
                DERIVED_KEY = bytes.fromhex(h[32:])
                break
            else: print("\033[91m 两次输入不一致，请重试！\033[0m")

        init_master_keys(DERIVED_KEY)
        ESP32_SECRET = secrets.token_hex(16)
        with open(KEY_FILE, "w", encoding="utf-8") as f: f.write(encrypt_data(ESP32_SECRET))
        print("\033[92m[OK] 密码设置成功！请务必牢记。\033[0m")
        time.sleep(1)
    else:
        print("\033[96m[NX-HLB] 请输入安全密码解锁\033[0m")
        while True:
            pwd = get_masked_input(" 密码: ")
            with open(AUTH_FILE, "r", encoding="utf-8") as f: stored_hash = f.read()
            if verify_password(stored_hash, pwd):
                DERIVED_KEY = bytes.fromhex(stored_hash[32:])
                init_master_keys(DERIVED_KEY)
                break
            else: print("\033[91m 密码错误，请重试！\033[0m")

        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f: enc_key = f.read()
            ESP32_SECRET = decrypt_data(enc_key)
            if not ESP32_SECRET: raise ValueError("解密失败")
        except Exception:
            print("\033[91m[错误] ESP32 密钥文件损坏或密码不匹配。\033[0m")
            confirm = input(" 重新生成会导致 ESP32 失联，输入 YES 确认: ")
            if confirm == "YES":
                ESP32_SECRET = secrets.token_hex(16)
                with open(KEY_FILE, "w", encoding="utf-8") as f: f.write(encrypt_data(ESP32_SECRET))
            else: sys.exit(1)

def change_password():
    global DERIVED_KEY, ESP32_SECRET
    print("\n\n [更改全局安全密码]")
    old_pwd = get_masked_input(" 请输入原密码: ")
    with open(AUTH_FILE, "r", encoding="utf-8") as f: stored_hash = f.read()

    if not verify_password(stored_hash, old_pwd):
        print("\033[91m 原密码错误！\033[0m"); time.sleep(1.5); return

    new_pwd1 = get_masked_input(" 设置新密码: ")
    new_pwd2 = get_masked_input(" 确认新密码: ")
    if not new_pwd1 or new_pwd1 != new_pwd2:
        print("\033[91m 两次输入不一致或为空，已取消。\033[0m"); time.sleep(1.5); return

    profiles = load_profiles()
    has_profiles_file = os.path.exists(PROFILES_FILE)
    if has_profiles_file and not profiles:
        confirm = input("\033[91m[警告] 旧预设文件解密失败，继续修改将清空旧预设，输入 YES 确认: \033[0m")
        if confirm != "YES": return

    new_hash = get_password_hash(new_pwd1)
    new_derived_key = bytes.fromhex(new_hash[32:])
    init_master_keys(new_derived_key)

    with open(AUTH_FILE, "w", encoding="utf-8") as f: f.write(new_hash)
    with open(KEY_FILE, "w", encoding="utf-8") as f: f.write(encrypt_data(ESP32_SECRET))

    if profiles:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            f.write(encrypt_data(json.dumps(profiles, ensure_ascii=False)))

    DERIVED_KEY = new_derived_key
    print("\033[92m[OK] 密码修改成功！请使用新密码登录。\033[0m")
    time.sleep(2.5)

# ================= 通信签名 (防止分号截断，改用 Base64 包裹 cmd) =================
def generate_secure_packet(cmd_body):
    b64_cmd = base64.b64encode(cmd_body.encode('utf-8')).decode('utf-8')
    nonce = secrets.token_hex(8)
    raw = f"{b64_cmd};{nonce}"
    sig = hmac.new(ESP32_SECRET.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{raw};{sig}"

def verify_secure_packet(packet):
    parts = packet.rsplit(';', 2)
    if len(parts) < 3: return None
    b64_cmd, nonce, sig = parts[0], parts[1], parts[2]
    if not nonce or len(nonce) > 64: return None

    raw = f"{b64_cmd};{nonce}"
    expected_sig = hmac.new(ESP32_SECRET.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig): return None

    with QUEUE_LOCK:
        if nonce in RECENT_NONCES: return None
        RECENT_NONCES.append(nonce)
        
    try: return base64.b64decode(b64_cmd).decode('utf-8')
    except: return None

# ================= 本地存储 =================
def load_profiles():
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f: enc_data = f.read()
            if not enc_data: return {}
            dec_data = decrypt_data(enc_data)
            if dec_data: return json.loads(dec_data)
        except: pass
    return {}

def save_profiles(profiles) -> bool:
    try:
        temp_file = PROFILES_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(encrypt_data(json.dumps(profiles, ensure_ascii=False)))
        os.replace(temp_file, PROFILES_FILE)
        return True
    except: return False

def next_preset_key(profiles):
    nums = [int(k) for k in profiles.keys() if k.isdigit()]
    return str(max(nums) + 1) if nums else "1"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

LOCAL_IP = get_local_ip()

# ================= 进程分流 =================
if len(sys.argv) == 1:
    if IS_FROZEN and os.path.exists("run.exe"):
        subprocess.Popen(["run.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif not IS_FROZEN and os.path.exists("run.py"):
        subprocess.Popen([sys.executable, "run.py"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    sys.exit(0)

if len(sys.argv) > 1 and sys.argv[1] == "--log-window":
    log_file = sys.argv[2]
    os.system("title NX-HLB 实时数据日志监控")
    os.system("color 0B")
    with open(log_file, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line: print(line, end="")
            else: time.sleep(0.1)

elif len(sys.argv) > 1 and sys.argv[1] == "--main-window":
    LOG_FILE = sys.argv[2]
    os.system("cls")
    init_security()
    os.system("cls")

    def write_log(msg, force_sync=False):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
                f.flush()
                if force_sync: os.fsync(f.fileno())
        except: pass

    def global_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback); return
        error_details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        write_log(f"========== 致命崩溃 ==========\n{error_details}", True)
        sys.exit(1)
    sys.excepthook = global_exception_handler

    def send_udp_command(cmd_body):
        global ESP32_IP
        if not ESP32_IP: return False
        secure_pkt = generate_secure_packet(cmd_body)
        payload = secure_pkt.encode()
        if len(payload) > 1400: return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(payload, (ESP32_IP, ESP32_PORT))
        sock.close()
        safe_log = '/set lan link <SSID> <PASSWORD> (已脱敏)' if cmd_body.startswith('/set lan link') else cmd_body[:60]
        write_log(f"[SEND] 下发安全指令: {safe_log}")
        return True

    def start_detection():
        if sys_status['detect_target'] is not None: return
        sys_status['fatal_error'] = False
        sys_status['fatal_msg'] = ""
        sys_status['action_msg'] = "正在同时检测小蓝盾与硬件..."
        sys_status['detect_target'] = "all"
        sys_status['progress'] = 30
        sys_status['need_redraw'] = True
        threading.Thread(target=run_detection_worker, daemon=True).start()

    def run_detection_worker():
        global TARGET_IP
        radmin_ok, hw_ok = False, False
        for i in range(30, 0, -1):
            if sys_status['detect_target'] != "all" or not sys_status['running']: return
            if not radmin_ok:
                try:
                    result = subprocess.run(['ipconfig'], capture_output=True, text=True, encoding='gbk', errors='ignore')
                    in_radmin = False
                    for line in result.stdout.split('\n'):
                        if "Radmin VPN" in line: in_radmin = True
                        elif in_radmin and "IPv4" in line:
                            TARGET_IP = line.split(':')[-1].strip()
                            sys_status['radmin'] = f"运行中 ({TARGET_IP})"
                            radmin_ok = True; break
                        elif in_radmin and "适配器" in line and "Radmin VPN" not in line: in_radmin = False
                except: pass
            
            if ESP32_IP:
                sys_status['hardware'] = f"已连接 ({ESP32_IP})"
                hw_ok = True

            if sys_status['detect_target'] != "all": return
            if radmin_ok and hw_ok:
                sys_status['action_msg'] = "环境检测全部通过！按 [2] 启动服务。"
                sys_status['detect_target'] = None
                sys_status['need_redraw'] = True
                return

            sys_status['progress'] = i
            sys_status['need_redraw'] = True
            time.sleep(1.0)
            
        if sys_status['detect_target'] != "all": return
        sys_status['detect_target'] = None
        if not radmin_ok:
            sys_status['radmin'] = "未检测到运行"
            sys_status['fatal_error'] = True
            sys_status['fatal_msg'] = "小蓝盾未启动或未加入网络。"
        elif not hw_ok:
            sys_status['hardware'] = "暂时无法连接至硬件"
            sys_status['fatal_error'] = True
            sys_status['fatal_msg'] = "未发现 ESP32 安全握手包。"
        sys_status['need_redraw'] = True

    def draw_ui():
        global LAST_VIEW
        if sys_status['view'] != LAST_VIEW:
            os.system('cls'); LAST_VIEW = sys_status['view']
        sys.stdout.write("\033[H")

        def print_line(text): sys.stdout.write(f"{text}\033[K\n")

        border_color = "\033[96m"
        r_col = "\033[92m" if "运行中" in sys_status['radmin'] else "\033[91m"
        h_col = "\033[92m" if "已连接" in sys_status['hardware'] else "\033[91m"
        s_col = "\033[92m" if sys_status['service_running'] else "\033[90m"
        s_text = "ON" if sys_status['service_running'] else "OFF"
        ip_warn = " \033[91m(IP异常,请检查内网)\033[0m" if LOCAL_IP == "127.0.0.1" else ""

        print_line(f"{border_color}{'=' * 60}\033[0m")
        print_line(f"  NX-HLB Hub {VERSION}                  PC IP: \033[96m{LOCAL_IP}\033[0m{ip_warn}")
        print_line(f"  {r_col}[小蓝盾] {sys_status['radmin']:<15}\033[0m {h_col}[硬件] {sys_status['hardware']:<18}\033[0m {s_col}[服务] {s_text}\033[0m")
        print_line(f"{border_color}{'=' * 60}\033[0m")

        if sys_status['view'] == "about":
            print_line("\n [关于与免责声明]")
            print_line("  1. 本项目仅供网络协议研究学习，严禁用于商业或非法用途。")
            print_line("  2. 本软件完全去中心化，绝不收集、上传任何用户隐私数据。")
            print_line("  3. 通信已启用 HMAC-SHA256 签名（支持特殊字符），本地数据已加密。")
            print_line("\n\n  [按 ESC 键] 返回主菜单")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J"); sys.stdout.flush(); sys_status['need_redraw'] = False
            return
            
        if sys_status['view'] == "key_export":
            print_line("\n [安全密钥配对]")
            print_line(" \033[91m注意：请勿截图外传此密钥。\033[0m")
            print_line(" 请将以下 C++ 代码复制，替换 Arduino 源码中的 SECRET_KEY：")
            print_line(f"\n \033[93mconst char* SECRET_KEY = \"{ESP32_SECRET}\";\033[0m\n")
            print_line("\n  [按 ESC 键] 返回主菜单")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J"); sys.stdout.flush(); sys_status['need_redraw'] = False
            return

        if sys_status['view'] == "preset_list":
            profiles = load_profiles()
            print_line("\n [WiFi 网络预设列表]")
            if not profiles: print_line("  (暂无预设，按 [A] 新增)")
            else:
                for idx, (k, v) in enumerate(profiles.items(), 1):
                    print_line(f"  [{idx}] {v['ssid']}")
            print_line(f"\n {border_color}{'-' * 60}\033[0m")
            print_line("  [数字键] 选择预设    [A] 新增预设    [ESC] 返回")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J"); sys.stdout.flush(); sys_status['need_redraw'] = False
            return

        if sys_status['view'] == "preset_detail":
            profiles = load_profiles()
            data = profiles.get(sys_status['selected_preset'])
            print_line(f"\n [预设详情 - {sys_status['selected_preset']}号]")
            if data:
                pwd_show = data['pwd'] if sys_status['show_password'] else "*" * len(data['pwd'])
                print_line(f"  WiFi 名称 : {data['ssid']}")
                print_line(f"  WiFi 密码 : {pwd_show}")
                print_line(f"\n {border_color}{'-' * 60}\033[0m")
                print_line("  [1] 显示/隐藏密码")
                print_line("  [2] 删除该预设")
                print_line("  [3] 下发该预设至 ESP32")
                print_line("  [ESC] 返回列表")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J"); sys.stdout.flush(); sys_status['need_redraw'] = False
            return

        if sys_status['fatal_error']: print_line(f"  \033[91m-> 错误原因 : {sys_status['fatal_msg']}\033[0m")
        else: print_line(f"  -> 运行模式 : 正常待命")
        
        print_line(f" {border_color}{'-' * 60}\033[0m")
        print_line(f" [功能菜单]")
        if sys_status['detect_target'] is not None: print_line(f"  \033[90m[1] 正在检测中... (按 ESC 取消)\033[0m")
        else: print_line(f"  \033[97m[1] 统一检测环境 (小蓝盾 + ESP32)\033[0m")
            
        if sys_status['service_running']: print_line(f"  {s_col}[2] 停止数据转发服务\033[0m")
        else: print_line(f"  \033[97m[2] 启动数据转发服务\033[0m")
            
        print_line(f"  \033[97m[3] 管理 WiFi 网络预设\033[0m")
        print_line(f"  \033[97m[4] 进入 ESP32 交互终端\033[0m")
        print_line(f"  \033[97m[5] 导出安全密钥 (配对用)\033[0m")
        print_line(f"  \033[97m[6] 更改全局安全密码\033[0m")
        print_line(f"  \033[97m[7] 关于与免责声明                 [Q] 安全退出\033[0m")
        print_line(f" {border_color}{'-' * 60}\033[0m")
        
        if sys_status['detect_target'] is not None:
            blocks = int((30 - sys_status['progress']) / 30 * 20)
            bar = "█" * blocks + "-" * (20 - blocks)
            print_line(f"  \033[96m检测进度: [{bar}] {sys_status['progress']}s\033[0m")
        else:
            print_line(f"  \033[93m{sys_status['action_msg']}\033[0m")
        
        print_line(f"{border_color}{'=' * 60}\033[0m")
        sys.stdout.write("\033[J"); sys.stdout.flush(); sys_status['need_redraw'] = False

    def run_terminal():
        global ESP32_IP, LAST_VIEW
        os.system('cls')
        print("\033[96m" + "="*70)
        print(f"  NX-HLB 交互终端 | 目标: \033[92m{ESP32_IP if ESP32_IP else '未连接'}\033[96m")
        print("  * 安全配对已启用。通信数据带有 HMAC-SHA256 签名。")
        print("-" * 70)
        print(" \033[93m[可用指令列表]\033[0m")
        print("  /help, /status, /reboot, /set lan link <名称> <密码>")
        print("=" * 70 + "\033[0m\n")

        if platform.system() == "Windows":
            current_input = ""
            sys.stdout.write("\033[92mESP32 > \033[0m" + current_input); sys.stdout.flush()

            while True:
                with QUEUE_LOCK:
                    if TERMINAL_QUEUE:
                        sys.stdout.write("\r\033[K")
                        for msg in TERMINAL_QUEUE: print(f"\033[95m[回传]\033[0m {msg}")
                        TERMINAL_QUEUE.clear()
                        sys.stdout.write("\033[92mESP32 > \033[0m" + current_input)
                        sys.stdout.flush()

                import msvcrt
                if msvcrt.kbhit():
                    ch = read_utf8_char()
                    if ch == '\x1b': break
                    elif ch == '\n':
                        if current_input.strip():
                            print() 
                            if not send_udp_command(current_input.strip()):
                                print("\033[91m[发送失败: ESP32 不在线]\033[0m")
                            current_input = ""
                        else: print() 
                        sys.stdout.write("\033[92mESP32 > \033[0m"); sys.stdout.flush()
                    elif ch == '\b':
                        if len(current_input) > 0:
                            # 处理多字节字符退格
                            current_input = current_input[:-1]
                            sys.stdout.write('\b \b'); sys.stdout.flush()
                    elif ch.isprintable():
                        current_input += ch
                        sys.stdout.write(ch); sys.stdout.flush()
                time.sleep(0.02)
                
        os.system('cls')
        import msvcrt
        while msvcrt.kbhit(): msvcrt.getch()
        LAST_VIEW = ""
        sys_status['need_redraw'] = True

    def handle_input():
        global ESP32_IP, LAST_VIEW
        if platform.system() == "Windows":
            import msvcrt
            if msvcrt.kbhit():
                key = read_utf8_char()
                
                if sys_status['detect_target'] is not None:
                    if key in ['\x1b', ' ']:
                        sys_status['detect_target'] = None
                        sys_status['progress'] = 0
                        sys_status['fatal_error'] = False
                        sys_status['fatal_msg'] = ""
                        sys_status['action_msg'] = "用户已强制取消检测。"
                        sys_status['need_redraw'] = True
                    return

                if key == '\x1b': 
                    if sys_status['view'] == "preset_detail": sys_status['view'] = "preset_list"
                    elif sys_status['view'] in ["preset_list", "about", "key_export"]: sys_status['view'] = "main"
                    sys_status['need_redraw'] = True
                    return
                
                if sys_status['view'] == "about":
                    sys_status['view'] = "main"; sys_status['need_redraw'] = True; return

                if sys_status['view'] == "preset_list":
                    if key in list("1234567890"):
                        profiles = load_profiles()
                        try:
                            idx = int(key) if key != '0' else 10
                            if idx <= len(profiles):
                                sys_status['selected_preset'] = list(profiles.keys())[idx-1]
                                sys_status['view'] = "preset_detail"
                                sys_status['show_password'] = False
                                sys_status['need_redraw'] = True
                        except: pass
                    elif key.lower() == 'a':
                        sys.stdout.write("\033[?25h"); os.system("cls")
                        print("\n\n [新增预设] (随时按 ESC 取消)")
                        ssid = get_user_input(" 请输入 WiFi 名称: ")
                        if ssid and ssid.strip():
                            pwd = get_user_input(" 请输入 WiFi 密码: ")
                            if pwd and pwd.strip():
                                profiles = load_profiles()
                                new_key = next_preset_key(profiles)
                                profiles[new_key] = {"ssid": ssid.strip(), "pwd": pwd.strip()}
                                if save_profiles(profiles): sys_status['action_msg'] = "新预设已保存。"
                                else: sys_status['action_msg'] = "错误: 预设保存失败!"
                            else: sys_status['action_msg'] = "已取消。"
                        else: sys_status['action_msg'] = "已取消。"
                        sys.stdout.write("\033[?25l"); os.system("cls")
                        LAST_VIEW = ""; sys_status['view'] = "preset_list"; sys_status['need_redraw'] = True
                    return

                if sys_status['view'] == "preset_detail":
                    if key == '1':
                        sys_status['show_password'] = not sys_status['show_password']
                        sys_status['need_redraw'] = True
                    elif key == '2':
                        sys.stdout.write("\033[?25h"); print("\n\n [删除确认] 确定要删除这个预设吗？(输入 Yes 确认)")
                        confirm = get_user_input(" -> ")
                        if confirm == "Yes":
                            profiles = load_profiles()
                            if sys_status['selected_preset'] in profiles:
                                del profiles[sys_status['selected_preset']]
                                if save_profiles(profiles): sys_status['action_msg'] = "预设已删除。"
                                else: sys_status['action_msg'] = "错误: 预设删除失败!"
                            sys_status['view'] = "preset_list"
                        else: sys_status['action_msg'] = "已取消删除。"
                        sys.stdout.write("\033[?25l"); os.system("cls")
                        LAST_VIEW = ""; sys_status['need_redraw'] = True
                    elif key == '3':
                        profiles = load_profiles()
                        data = profiles.get(sys_status['selected_preset'])
                        if data:
                            ssid_b = data["ssid"].encode('utf-8')
                            pwd_b = data["pwd"].encode('utf-8')
                            cmd = f'/set lan link {len(ssid_b)}:{data["ssid"]} {len(pwd_b)}:{data["pwd"]}'
                            ok = send_udp_command(cmd)
                            sys_status['action_msg'] = "安全指令下发成功" if ok else "ESP32 不在线"
                            sys_status['need_redraw'] = True
                    return

                key_lower = key.lower()
                if key_lower == 'q': sys_status['running'] = False
                elif key == '1': start_detection()
                elif key == '2':
                    if sys_status['service_running']:
                        sys_status['service_running'] = False
                        sys_status['action_msg'] = "服务已停止。"
                    else:
                        if "运行中" in sys_status['radmin'] and "已连接" in sys_status['hardware']:
                            sys_status['service_running'] = True
                            sys_status['action_msg'] = "服务已启动！正在转发数据..."
                            sys_status['fatal_error'] = False
                        else:
                            sys_status['fatal_error'] = True
                            sys_status['fatal_msg'] = "无法启动服务：前置环境未满足。"
                            sys_status['action_msg'] = "请先按 [1] 确保环境正常。"
                    sys_status['need_redraw'] = True
                elif key == '3': sys_status['view'] = "preset_list"; sys_status['need_redraw'] = True
                elif key == '4': run_terminal() 
                elif key == '5': sys_status['view'] = "key_export"; sys_status['need_redraw'] = True
                elif key == '6': 
                    sys_status['is_paused'] = True; os.system('cls')
                    change_password()
                    sys.stdout.write("\033[?25l"); os.system("cls")
                    LAST_VIEW = ""; sys_status['is_paused'] = False; sys_status['need_redraw'] = True
                elif key == '7': sys_status['view'] = "about"; sys_status['need_redraw'] = True

    def receive_from_hardware():
        global ESP32_IP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", LISTEN_PORT))
        
        while sys_status['running']:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(2048)
                raw_msg = data.decode('utf-8', errors='ignore')
                
                msg = verify_secure_packet(raw_msg)
                if msg is None:
                    write_log(f"[SEC] 丢弃无效包 from {addr[0]}:{addr[1]} ({len(data)}B)")
                    continue
                
                if ESP32_IP != addr[0]:
                    ESP32_IP = addr[0]
                    write_log(f"[NET] 发现合法 ESP32: {ESP32_IP}")
                    # 修复重启死脑筋：回复 IP 的同时，带上 PC_REBOOT 信号，让 ESP32 刷新状态
                    sock.sendto(generate_secure_packet(f"PC_IP:{LOCAL_IP}").encode(), (ESP32_IP, ESP32_PORT))
                    sys_status['hardware'] = f"已连接 ({ESP32_IP})"
                    sys_status['need_redraw'] = True

                if "DISCOVERY" in msg: continue
                
                if msg.startswith("[DEV_MSG]"):
                    txt = msg.replace("[DEV_MSG]", "").strip()
                    with QUEUE_LOCK: TERMINAL_QUEUE.append(txt)
                    continue

                if sys_status['service_running'] and "运行中" in sys_status['radmin'] and addr[0] == ESP32_IP:
                    if TARGET_IP and TARGET_IP != "127.0.0.1":
                        forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        forward_sock.sendto(data, (TARGET_IP, RADMIN_PORT))
                        forward_sock.close()
                        write_log(f"[SERVICE] 转发数据包 {len(data)} 字节。")
                    else: write_log(f"[WARN] 转发失败: TARGET_IP 无效")
                    
            except socket.timeout: pass
            except Exception: pass

    try:
        write_log(f"========== NX-HLB 安全中枢启动 ==========", True)
        sys_status['need_redraw'] = True
        draw_ui() 
        threading.Thread(target=receive_from_hardware, daemon=True).start()
        start_detection()
        
        # 修复重启死脑筋：PC 端启动时，先向局域网喊一声，让 ESP32 刷新 PC IP
        try:
            bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            bcast_sock.sendto(generate_secure_packet("PC_REBOOT").encode(), ('<broadcast>', ESP32_PORT))
            bcast_sock.close()
            write_log("[NET] 已发送 PC_REBOOT 广播，通知 ESP32 刷新连接。")
        except: pass

        while sys_status['running']:
            if sys_status['need_redraw']: draw_ui()
            handle_input()
            time.sleep(0.05) 
        sys.stdout.write("\033[?25l")
        sys.exit(0)
    except KeyboardInterrupt:
        write_log("用户手动强制退出程序 (Ctrl+C)", True)
        sys.exit(0)
    except Exception as e:
        write_log(f"崩溃: {str(e)}\n{traceback.format_exc()}", True)
        sys.exit(1)