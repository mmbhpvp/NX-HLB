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

VERSION = "v0.2.3"
LISTEN_PORT = 11451
TARGET_PORT = 11452
TARGET_IP = "127.0.0.1"
ESP32_IP = None 

DATA_DIR = "NX-HLB_Data"
PROFILES_FILE = os.path.join(DATA_DIR, "wifi_profiles.enc")
LAST_VIEW = ""
SECRET_KEY = "NX_HLB_SECRET_2026"

# 终端消息队列（用于多线程通信）
TERMINAL_QUEUE = []
QUEUE_LOCK = threading.Lock()

IS_FROZEN = getattr(sys, 'frozen', False)
sys_status = {
    "radmin": "未检测到运行", 
    "hardware": "暂时无法连接至硬件", 
    "service_running": False,
    "running": True, 
    "fatal_error": False,
    "fatal_msg": "", 
    "action_msg": "系统启动，正在自动初始化检测...",
    "view": "main", 
    "need_redraw": False, 
    "detect_target": None, 
    "progress": 0,
    "selected_preset": None,
    "show_password": False
}

# ================= 加密与数据管理 =================
def xor_encrypt_decrypt(text):
    return ''.join(chr(ord(c) ^ ord(SECRET_KEY[i % len(SECRET_KEY)])) for i, c in enumerate(text))

def load_profiles():
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                enc_data = f.read()
                if not enc_data: return {}
                dec_data = xor_encrypt_decrypt(base64.b64decode(enc_data).decode('utf-8'))
                return json.loads(dec_data)
        except: pass
    return {} 

def save_profiles(profiles):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    try:
        json_str = json.dumps(profiles, ensure_ascii=False)
        enc_data = base64.b64encode(xor_encrypt_decrypt(json_str).encode('utf-8')).decode('utf-8')
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            f.write(enc_data)
    except Exception as e:
        pass

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()

# ================= 高级输入函数 =================
def get_user_input(prompt=""):
    print(prompt, end="", flush=True)
    result = ""
    if platform.system() == "Windows":
        import msvcrt
        while True:
            ch = msvcrt.getch()
            if ch == b'\x1b':
                print("\n [已取消]")
                return None
            elif ch in [b'\r', b'\n']:
                print()
                return result
            elif ch == b'\x08':
                if len(result) > 0:
                    result = result[:-1]
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            else:
                try:
                    char = ch.decode('utf-8')
                    if char.isprintable():
                        result += char
                        sys.stdout.write(char)
                        sys.stdout.flush()
                except: pass
    else:
        try: return input(prompt)
        except: return None

# ================= 防呆与进程分流 =================
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
    
    def write_log(msg):
        try:
            if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
                f.flush(); os.fsync(f.fileno())
        except: pass

    def global_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback); return
        error_details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        write_log(f"========== 致命崩溃 ==========\n{error_details}")
        sys.exit(1)
    sys.excepthook = global_exception_handler

    def send_udp_command(cmd):
        global ESP32_IP
        if ESP32_IP:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd.encode(), (ESP32_IP, TARGET_PORT))
            sock.close()
            write_log(f"[SEND] 已下发指令: {cmd}")
            return True
        else:
            return False

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
        radmin_ok = False
        hw_ok = False
        
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
            sys_status['fatal_msg'] = "未发现 ESP32 硬件数据包。"
        sys_status['need_redraw'] = True

    def draw_ui():
        global LAST_VIEW
        if sys_status['view'] != LAST_VIEW:
            os.system('cls')
            LAST_VIEW = sys_status['view']
            
        sys.stdout.write("\033[H")
        def print_line(text):
            sys.stdout.write(f"{text}\033[K\n")

        border_color = "\033[96m"
        
        r_col = "\033[92m" if "运行中" in sys_status['radmin'] else "\033[91m"
        h_col = "\033[92m" if "已连接" in sys_status['hardware'] else "\033[91m"
        s_col = "\033[92m" if sys_status['service_running'] else "\033[90m"
        s_text = "ON" if sys_status['service_running'] else "OFF"

        print_line(f"{border_color}{'=' * 60}\033[0m")
        print_line(f"  NX-HLB Hub {VERSION}                  PC IP: \033[96m{LOCAL_IP}\033[0m")
        print_line(f"  {r_col}[小蓝盾] {sys_status['radmin']:<15}\033[0m {h_col}[硬件] {sys_status['hardware']:<18}\033[0m {s_col}[服务] {s_text}\033[0m")
        print_line(f"{border_color}{'=' * 60}\033[0m")

        if sys_status['view'] == "about":
            print_line("\n [关于与免责声明]")
            print_line("  1. 本项目仅供网络协议研究学习，严禁用于商业或非法用途。")
            print_line("  2. 本软件完全去中心化，绝不收集、上传任何用户的网络信息与隐私数据。")
            print_line("  3. 任何因使用本工具导致的网络波动、账号异常，由使用者自行承担。")
            print_line("  4. 本项目与任天堂（Nintendo）无任何关联。")
            print_line("\n\n  [按 ESC 键] 返回主菜单")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J")
            sys.stdout.flush()
            sys_status['need_redraw'] = False
            return

        if sys_status['view'] == "preset_list":
            profiles = load_profiles()
            print_line("\n [WiFi 网络预设列表 (加密存储)]")
            if not profiles:
                print_line("  (暂无预设，按 [A] 新增)")
            else:
                for idx, (k, v) in enumerate(profiles.items(), 1):
                    print_line(f"  [{idx}] {v['ssid']}")
            
            print_line(f"\n {border_color}{'-' * 60}\033[0m")
            print_line("  [数字键] 选择预设    [A] 新增预设    [ESC] 返回")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J") 
            sys.stdout.flush()
            sys_status['need_redraw'] = False
            return

        if sys_status['view'] == "preset_detail":
            profiles = load_profiles()
            idx = sys_status['selected_preset']
            data = profiles.get(idx)
            
            print_line(f"\n [预设详情 - {idx}号]")
            if data:
                pwd_show = data['pwd'] if sys_status['show_password'] else "*" * len(data['pwd'])
                print_line(f"  WiFi 名称 : {data['ssid']}")
                print_line(f"  WiFi 密码 : {pwd_show}")
                print_line(f"\n {border_color}{'-' * 60}\033[0m")
                print_line("  [1] 显示/隐藏密码")
                print_line("  [2] 删除该预设")
                print_line("  [3] 下发该预设至 ESP32")
                print_line("  [ESC] 返回列表")
            else:
                print_line("  预设已不存在。")
                print_line("  [ESC] 返回列表")
            print_line(f"{border_color}{'=' * 60}\033[0m")
            sys.stdout.write("\033[J") 
            sys.stdout.flush()
            sys_status['need_redraw'] = False
            return

        # 主界面
        if sys_status['fatal_error']:
            print_line(f"  \033[91m-> 错误原因 : {sys_status['fatal_msg']}\033[0m")
        else:
            print_line(f"  -> 运行模式 : 正常待命")
        
        print_line(f" {border_color}{'-' * 60}\033[0m")
        print_line(f" [功能菜单]")
        
        if sys_status['detect_target'] is not None:
            print_line(f"  \033[90m[1] 正在检测中... (按 ESC 取消)\033[0m") # UI修改为按 ESC 取消
        else:
            print_line(f"  \033[97m[1] 统一检测环境 (小蓝盾 + ESP32)\033[0m")
            
        if sys_status['service_running']:
            print_line(f"  {s_col}[2] 停止数据转发服务\033[0m")
        else:
            print_line(f"  \033[97m[2] 启动数据转发服务\033[0m")
            
        print_line(f"  \033[97m[3] 管理 WiFi 网络预设\033[0m")
        print_line(f"  \033[97m[4] 进入 ESP32 交互终端\033[0m")
        print_line(f"  \033[97m[5] 关于与免责声明                 [Q] 安全退出\033[0m")
        print_line(f" {border_color}{'-' * 60}\033[0m")
        
        if sys_status['detect_target'] is not None:
            blocks = int((30 - sys_status['progress']) / 30 * 20)
            bar = "█" * blocks + "-" * (20 - blocks)
            print_line(f"  \033[96m检测进度: [{bar}] {sys_status['progress']}s\033[0m")
        else:
            print_line(f"  \033[93m{sys_status['action_msg']}\033[0m")
        
        print_line(f"{border_color}{'=' * 60}\033[0m")
        
        # 终极防打架机制：清除光标后方所有内容（避免上一帧残留）
        sys.stdout.write("\033[J") 
        sys.stdout.flush()
        sys_status['need_redraw'] = False

    def run_terminal():
        """实时回传的沉浸式终端"""
        global ESP32_IP, LAST_VIEW, TERMINAL_QUEUE
        os.system('cls')
        
        print("\033[96m" + "="*70)
        print(f"  NX-HLB 交互终端 | 目标: \033[92m{ESP32_IP if ESP32_IP else '未连接'}\033[96m")
        print("  输入指令按回车发送，随时按 ESC 退出。支持输入时实时接收硬件日志。")
        print("-" * 70)
        print(" \033[93m[可用指令列表]\033[0m")
        print("  /help                              - 显示此帮助菜单")
        print("  /status                            - 查看当前网络与信号状态")
        print("  /reboot                            - 重启 ESP32 设备")
        print("  /set lan link <名称> <密码>         - 设置并保存新 WiFi (支持带空格)")
        print("=" * 70 + "\033[0m\n")

        if platform.system() == "Windows":
            import msvcrt
            current_input = ""
            
            sys.stdout.write("\033[92mESP32 > \033[0m" + current_input)
            sys.stdout.flush()

            while True:
                with QUEUE_LOCK:
                    if TERMINAL_QUEUE:
                        sys.stdout.write("\r\033[K")
                        for msg in TERMINAL_QUEUE:
                            print(f"\033[95m[回传]\033[0m {msg}")
                        TERMINAL_QUEUE.clear()
                        sys.stdout.write("\033[92mESP32 > \033[0m" + current_input)
                        sys.stdout.flush()

                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    
                    if ch == b'\x1b':  # ESC 退出终端
                        break
                    
                    elif ch in [b'\r', b'\n']:  # 回车发送
                        if current_input.strip():
                            print() 
                            ok = send_udp_command(current_input.strip())
                            if not ok:
                                print("\033[91m[发送失败: ESP32 不在线]\033[0m")
                            current_input = ""
                        else:
                            print() 
                        
                        sys.stdout.write("\033[92mESP32 > \033[0m")
                        sys.stdout.flush()
                        
                    elif ch == b'\x08':  # 退格
                        if len(current_input) > 0:
                            current_input = current_input[:-1]
                            sys.stdout.write('\b \b')
                            sys.stdout.flush()
                            
                    else:  # 普通字符
                        try:
                            char = ch.decode('utf-8')
                            if char.isprintable():
                                current_input += char
                                sys.stdout.write(char)
                                sys.stdout.flush()
                        except: pass
                
                time.sleep(0.02)
                    
        os.system('cls')
        LAST_VIEW = ""
        sys_status['need_redraw'] = True

    def handle_input():
        global ESP32_IP, LAST_VIEW, TERMINAL_QUEUE
        if platform.system() == "Windows":
            import msvcrt
            if msvcrt.kbhit():
                # 获取按键
                raw_key = msvcrt.getch()
                key = raw_key.decode('utf-8', errors='ignore').lower()
                
                # 1. 处理检测中的打断逻辑
                if sys_status['detect_target'] is not None:
                    # 将取消检测的按键统一为 ESC
                    if raw_key == b'\x1b' or key == ' ':
                        sys_status['detect_target'] = None
                        sys_status['progress'] = 0
                        sys_status['action_msg'] = "用户已强制取消检测。"
                        sys_status['need_redraw'] = True
                    return

                # 2. 处理通用的 ESC 返回逻辑
                if raw_key == b'\x1b': 
                    if sys_status['view'] == "preset_detail":
                        sys_status['view'] = "preset_list"
                    elif sys_status['view'] in ["preset_list", "about"]:
                        sys_status['view'] = "main"
                    sys_status['need_redraw'] = True
                    return
                
                # 3. 处理普通视图的返回
                if sys_status['view'] == "about":
                    sys_status['view'] = "main"
                    sys_status['need_redraw'] = True
                    return

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
                    elif key == 'a':
                        sys_status['view'] = "preset_add"
                        sys.stdout.write("\033[?25h")
                        os.system("cls")
                        print("\n\n [新增预设] (随时按 ESC 取消)")
                        ssid = get_user_input(" 请输入 WiFi 名称: ")
                        if ssid is not None and ssid.strip():
                            pwd = get_user_input(" 请输入 WiFi 密码: ")
                            if pwd is not None and pwd.strip():
                                profiles = load_profiles()
                                new_key = str(len(profiles) + 1)
                                profiles[new_key] = {"ssid": ssid.strip(), "pwd": pwd.strip()}
                                save_profiles(profiles)
                                sys_status['action_msg'] = "新预设已加密保存。"
                            else: sys_status['action_msg'] = "已取消新增。"
                        else: sys_status['action_msg'] = "已取消新增。"
                            
                        sys.stdout.write("\033[?25l")
                        os.system("cls")
                        LAST_VIEW = ""
                        sys_status['view'] = "preset_list"
                        sys_status['need_redraw'] = True
                    return

                if sys_status['view'] == "preset_detail":
                    if key == '1':
                        sys_status['show_password'] = not sys_status['show_password']
                        sys_status['need_redraw'] = True
                    elif key == '2':
                        sys_status['view'] = "preset_delete"
                        sys.stdout.write("\033[?25h")
                        print("\n\n [删除确认] 确定要删除这个预设吗？(输入 Yes 确认)")
                        confirm = get_user_input(" -> ")
                        if confirm == "Yes":
                            profiles = load_profiles()
                            if sys_status['selected_preset'] in profiles:
                                del profiles[sys_status['selected_preset']]
                                new_profiles = {str(i+1): v for i, (k, v) in enumerate(profiles.items())}
                                save_profiles(new_profiles)
                            sys_status['action_msg'] = "预设已删除。"
                            sys_status['view'] = "preset_list"
                        else:
                            sys_status['action_msg'] = "已取消删除。"
                            sys_status['view'] = "preset_detail"
                        sys.stdout.write("\033[?25l")
                        os.system("cls")
                        LAST_VIEW = "" 
                        sys_status['need_redraw'] = True
                    elif key == '3':
                        profiles = load_profiles()
                        data = profiles.get(sys_status['selected_preset'])
                        if data:
                            cmd = f"/set lan link {data['ssid']} {data['pwd']}"
                            ok = send_udp_command(cmd)
                            sys_status['action_msg'] = "指令下发成功" if ok else "ESP32 不在线"
                            sys_status['need_redraw'] = True
                    return

                # 主界面快捷键
                if key == 'q':
                    sys_status['running'] = False
                elif key == '1':
                    start_detection()
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
                elif key == '3':
                    sys_status['view'] = "preset_list"
                    sys_status['need_redraw'] = True
                elif key == '4':
                    sys_status['view'] = "terminal"
                    sys_status['need_redraw'] = False
                    run_terminal() 
                elif key == '5':
                    sys_status['view'] = "about"
                    sys_status['need_redraw'] = True

    def receive_from_hardware():
        global ESP32_IP, TERMINAL_QUEUE
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", LISTEN_PORT))
        
        while sys_status['running']:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(2048)
                msg = data.decode('utf-8', errors='ignore')
                
                if ESP32_IP != addr[0]:
                    ESP32_IP = addr[0]
                    write_log(f"[NET] 发现/更新 ESP32 IP: {ESP32_IP}")
                    sock.sendto(f"PC_IP:{LOCAL_IP}".encode(), (ESP32_IP, TARGET_PORT))
                    sys_status['hardware'] = f"已连接 ({ESP32_IP})"
                    sys_status['need_redraw'] = True

                if "ESP32_DISCOVERY" in msg: continue
                
                if msg.startswith("[DEV_MSG]"):
                    txt = msg.replace("[DEV_MSG]", "").strip()
                    with QUEUE_LOCK:
                        TERMINAL_QUEUE.append(txt)
                    continue

                if sys_status['service_running'] and "运行中" in sys_status['radmin']:
                    forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    forward_sock.sendto(data, (TARGET_IP, TARGET_PORT))
                    forward_sock.close()
                    write_log(f"[SERVICE] 转发数据包 {len(data)} 字节至小蓝盾。")
                    
            except socket.timeout:
                pass
            except Exception:
                pass

    try:
        os.system('cls')
        write_log(f"========== NX-HLB 中枢启动 ==========")
        sys_status['need_redraw'] = True
        draw_ui() 
        
        threading.Thread(target=receive_from_hardware, daemon=True).start()
        start_detection()
        
        while sys_status['running']:
            if sys_status['need_redraw']:
                draw_ui()
            handle_input()
            time.sleep(0.05) 
            
        sys.stdout.write("\033[?25h")
        sys.exit(0)
    except Exception as e:
        write_log(f"崩溃: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)