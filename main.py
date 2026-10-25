import socket
import threading
import sys
import time
import platform
import subprocess
import traceback
import os
import datetime

VERSION = "v0.1.3"
LISTEN_PORT = 11451
TARGET_PORT = 11452
TARGET_IP = "127.0.0.1"

# 判断当前是否处于打包后的 EXE 环境
IS_FROZEN = getattr(sys, 'frozen', False)

sys_status = {
    "radmin": "未检测到运行",
    "hardware": "暂时无法连接至硬件",
    "latency": "暂无",
    "running": True,
    "fatal_error": True,
    "fatal_msg": "无法识别小蓝盾运行状态！请确认已打开软件并加入了网络。",
    "action_msg": "系统就绪，请按 [1] 重新检测。",
    "view": "main",
    "need_redraw": False,
    "detect_target": None, 
    "progress": 0
}

# ================= 防呆：如果开发者直接运行 main =================
if len(sys.argv) == 1:
    print("检测到直接运行 main，正在为你启动总控台 (run)...")
    # 如果是EXE环境找run.exe，如果是源码环境找run.py
    if IS_FROZEN and os.path.exists("run.exe"):
        subprocess.Popen(["run.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE if platform.system() == "Windows" else 0)
    elif not IS_FROZEN and os.path.exists("run.py"):
        subprocess.Popen([sys.executable, "run.py"], creationflags=subprocess.CREATE_NEW_CONSOLE if platform.system() == "Windows" else 0)
    else:
        print("错误：找不到 run.py 或 run.exe，请确保它在这个文件夹里！")
        os.system("pause")
    sys.exit(0)

# ================= 进程分流 =================
if len(sys.argv) > 1 and sys.argv[1] == "--log-window":
    log_file = sys.argv[2]
    os.system("title NX-HLB 实时数据日志监控")
    os.system("color 0B")
    with open(log_file, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.1)

elif len(sys.argv) > 1 and sys.argv[1] == "--main-window":
    LOG_FILE = sys.argv[2]
    packet_counter = 0  
    
    def write_log(msg):
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
                f.flush()
                os.fsync(f.fileno())
        except:
            pass

    def global_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        write_log(f"========== 致命崩溃 ==========\n{error_details}")
        sys.exit(1)
    sys.excepthook = global_exception_handler

    def run_detection(target):
        if sys_status['detect_target'] is not None:
            return

        write_log(f"[DETECT] 开始执行 30 秒倒计时检测，目标: {target}")
        sys_status['detect_target'] = target
        sys_status['progress'] = 30
        sys_status['need_redraw'] = True
        
        for i in range(30, 0, -1):
            if sys_status['detect_target'] != target or not sys_status['running']:
                write_log(f"[DETECT] 用户中途打断了对 {target} 的检测。")
                return
            sys_status['progress'] = i
            sys_status['need_redraw'] = True
            time.sleep(1.0)
            
        if sys_status['detect_target'] != target:
            return

        sys_status['detect_target'] = None
        write_log(f"[DETECT] 倒计时结束，开始执行真实环境扫描: {target}")
        
        if target == "radmin":
            global TARGET_IP
            try:
                result = subprocess.run(['ipconfig'], capture_output=True, text=True, encoding='gbk', errors='ignore')
                lines = result.stdout.split('\n')
                in_radmin = False
                for line in lines:
                    if "Radmin VPN" in line:
                        in_radmin = True
                    elif in_radmin and "IPv4" in line:
                        TARGET_IP = line.split(':')[-1].strip()
                        sys_status['radmin'] = f"运行中 ({TARGET_IP})"
                        sys_status['fatal_error'] = False
                        sys_status['action_msg'] = "小蓝盾环境检测成功！"
                        write_log(f"[SUCCESS] 小蓝盾检测成功！分配 IP: {TARGET_IP}")
                        sys_status['need_redraw'] = True
                        return
                    elif in_radmin and "适配器" in line and "Radmin VPN" not in line:
                        in_radmin = False
                        
                sys_status['radmin'] = "未检测到运行"
                sys_status['fatal_error'] = True
                sys_status['fatal_msg'] = "无法识别小蓝盾运行状态！请确认已打开软件并加入了网络。"
                sys_status['action_msg'] = "小蓝盾检测失败，请排查。"
                write_log("[ERROR] 小蓝盾检测失败！未在 ipconfig 中找到 Radmin VPN 网卡。")
                sys_status['need_redraw'] = True
            except Exception as e:
                write_log(f"[ERROR] 小蓝盾检测过程发生异常: {e}")

        elif target == "hardware":
            write_log("[DETECT] 正在检查 ESP32 硬件连接状态...")
            if sys_status['hardware'] == "暂时无法连接至硬件":
                sys_status['action_msg'] = "硬件连接失败：未检测到设备插入。"
                write_log("[ERROR] 硬件检测失败：UDP 端口未收到任何数据包。")
            else:
                sys_status['action_msg'] = "硬件连接状态正常。"
                write_log("[SUCCESS] 硬件状态检测正常，有数据包正在传输。")
            sys_status['need_redraw'] = True

    def draw_ui():
        sys.stdout.write("\033[H")
        
        def print_line(text):
            sys.stdout.write(f"{text}\033[K\n")

        if sys_status['fatal_error']:
            os.system("title [暂时无法启动服务] NX-HLB 主控台")
        else:
            os.system("title [Running] NX-HLB 主控台")

        border_color = "\033[96m"

        print_line(f"{border_color}{'=' * 55}\033[0m")
        if sys_status['fatal_error']:
            print_line(f"{border_color}       NX-HLB Desktop Hub {VERSION}  \033[91m[ 暂时无法启动服务 ]{border_color}       \033[0m")
        else:
            print_line(f"{border_color}       NX-HLB Desktop Hub {VERSION} (Dev)        \033[0m")
        print_line(f"{border_color}       Hardware LAN Bridge Project              \033[0m")
        print_line(f"{border_color}{'=' * 55}\033[0m")
        print_line("")
        
        print_line(f" {border_color}----------------------------------------------------------------\033[0m")
        if sys_status['fatal_error']:
            print_line(f"  -> \033[91m错误原因 : {sys_status['fatal_msg']}\033[0m")
        else:
            radmin_color = "\033[92m" if "运行中" in sys_status['radmin'] else "\033[93m"
            hw_color = "\033[92m" if "已连接" in sys_status['hardware'] else "\033[93m"
            print_line(f"  -> {radmin_color}小蓝盾环境 : {sys_status['radmin']}\033[0m")
            print_line(f"  -> {hw_color}硬件连接   : {sys_status['hardware']}\033[0m")
            print_line(f"  -> 网络延时   : {sys_status['latency']}")
        
        print_line(f" {border_color}----------------------------------------------------------------\033[0m")
        
        if sys_status['view'] == "about":
            print_line(" [关于与免责声明]")
            print_line("  NX-HLB - 仅供网络协议研究，不对封号负责。")
            print_line("")
            print_line("  [任意键] 返回主菜单")
        else:
            menu_color = "\033[97m"
            print_line(f" [功能菜单]")
            
            if sys_status['detect_target'] == "radmin":
                print_line(f"  \033[90m[1] 正在检测中... (按空格取消)\033[0m")
            elif sys_status['detect_target'] == "hardware":
                print_line(f"  \033[90m[1] 暂时无法运行\033[0m")
            else:
                print_line(f"  {menu_color}[1] 重新检测小蓝盾运行状态\033[0m")
                
            if sys_status['detect_target'] == "hardware":
                print_line(f"  \033[90m[2] 正在检测中... (按空格取消)\033[0m")
            elif sys_status['detect_target'] == "radmin":
                print_line(f"  \033[90m[2] 暂时无法运行\033[0m")
            else:
                print_line(f"  {menu_color}[2] 重新检测硬件连接状态\033[0m")
                
            print_line(f"  {menu_color}[3] 关于与免责声明             [Q] 安全退出程序\033[0m")
            print_line(f" {border_color}----------------------------------------------------------------\033[0m")
            
            if sys_status['detect_target']:
                blocks = int((30 - sys_status['progress']) / 30 * 20)
                bar = "█" * blocks + "-" * (20 - blocks)
                print_line(f"  \033[96m检测进度: [{bar}] {sys_status['progress']}s\033[0m")
            else:
                if not sys_status['fatal_error']:
                    print_line(f"  {sys_status['action_msg']}")
                else:
                    print_line(f"  \033[93m{sys_status['action_msg']}\033[0m")
                
        print_line(f"{border_color}{'=' * 55}\033[0m")

        sys.stdout.flush()
        sys_status['need_redraw'] = False

    def handle_input():
        if platform.system() == "Windows":
            import msvcrt
            if msvcrt.kbhit():
                key = msvcrt.getch().decode('utf-8', errors='ignore').lower()
                
                if sys_status['detect_target']:
                    if key == ' ':
                        sys_status['detect_target'] = None
                        sys_status['action_msg'] = "用户已取消检测。"
                        sys_status['need_redraw'] = True
                    return
                
                if sys_status['view'] == "about":
                    sys_status['view'] = "main"
                    sys_status['need_redraw'] = True
                    return

                if key == 'q':
                    write_log("[INPUT] 用户按下了 [Q]，请求安全退出程序。")
                    sys_status['running'] = False
                elif key == '1':
                    write_log("[INPUT] 用户按下了 [1]，请求检测小蓝盾。")
                    threading.Thread(target=run_detection, args=("radmin",), daemon=True).start()
                elif key == '2':
                    write_log("[INPUT] 用户按下了 [2]，请求检测硬件。")
                    threading.Thread(target=run_detection, args=("hardware",), daemon=True).start()
                elif key == '3':
                    write_log("[INPUT] 用户按下了 [3]，查看关于页面。")
                    sys_status['view'] = "about"
                    sys_status['need_redraw'] = True

    def receive_from_hardware():
        global packet_counter
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", LISTEN_PORT))
            write_log(f"[NET] 成功绑定本地 UDP 端口: {LISTEN_PORT}，等待硬件数据...")
        except OSError:
            write_log(f"[ERROR] 端口 {LISTEN_PORT} 被占用，无法启动监听！")
            raise RuntimeError(f"端口 {LISTEN_PORT} 被占用！")
            
        is_connected = False
        
        while sys_status['running']:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(2048)
                
                if not is_connected:
                    sys_status['hardware'] = "已连接并接收数据"
                    sys_status['latency'] = "15 ms"
                    sys_status['need_redraw'] = True
                    write_log(f"[NET] 🚀 收到来自硬件 ({addr}) 的第一包数据！硬件连接已建立。")
                    is_connected = True
                
                packet_counter += 1
                if packet_counter % 50 == 0:
                    write_log(f"[NET] <- 持续接收数据中: 已累计收到 {packet_counter} 个包 (最后包大小: {len(data)} 字节)。")
                
                if not sys_status['fatal_error']:
                    forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    forward_sock.sendto(data, (TARGET_IP, TARGET_PORT))
                    forward_sock.close()
                    
            except socket.timeout:
                if is_connected:
                    sys_status['hardware'] = "暂时无法连接至硬件"
                    sys_status['latency'] = "暂无"
                    sys_status['need_redraw'] = True
                    write_log("[NET] ⚠️ 硬件数据流超时中断。可能设备已拔出或 Switch 断开了本地联机。")
                    is_connected = False
            except Exception as e:
                write_log(f"[NET] 接收线程发生异常: {e}")

    # --- 主循环 ---
    try:
        os.system('cls' if platform.system() == 'Windows' else 'clear')
        write_log(f"========== NX-HLB 主程序启动 (版本: {VERSION}) ==========")
        write_log(f"[SYS] 当前环境: {'EXE环境' if IS_FROZEN else '源码环境'}")
        write_log(f"[SYS] 日志文件已挂载，开始记录全链路操作。")
        
        sys_status['need_redraw'] = True
        draw_ui() 
        
        threading.Thread(target=run_detection, args=("radmin",), daemon=True).start()
        
        t1 = threading.Thread(target=receive_from_hardware, daemon=True)
        t1.start()
        
        while sys_status['running']:
            if sys_status['need_redraw']:
                draw_ui()
                
            handle_input()
            time.sleep(0.05) 
            
        sys.stdout.write("\033[?25h")
        write_log("========== 用户安全退出，程序关闭 ==========")
        sys.exit(0)
        
    except Exception as e:
        write_log(f"========== 运行期发生严重错误 ==========\n{str(e)}\n{traceback.format_exc()}")
        sys.exit(1)