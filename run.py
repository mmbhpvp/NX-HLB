import subprocess
import sys
import os
import time
import datetime
import platform

VERSION = "v0.4.3_Final"
# 严格对齐 main.py 的数据目录
DATA_DIR = "NX-HLB_Data"

def cleanup_process(proc, name):
    """终极清理：强杀进程树，确保不留下任何幽灵黑框"""
    if proc and proc.poll() is None:
        print(f"[*] 正在回收 {name} (PID: {proc.pid})...")
        try:
            if platform.system() == "Windows":
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        except Exception as e:
            print(f"[!] 回收 {name} 失败: {e}")

def main():
    # 1. 确保数据文件夹存在
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 2. 生成带时间戳的日志文件
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.abspath(os.path.join(DATA_DIR, f"NX-HLB_Log_{timestamp}.log"))
    
    # 初始化日志文件
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [BOOT] Run 守护进程启动，日志初始化。\n")
    except Exception as e:
        print(f"[!] 无法创建日志文件: {e}")
        os.system("pause")
        return

    py_exe = sys.executable
    main_script = os.path.abspath("main.py")
    
    # 兼容打包后的 EXE 环境
    is_main_exe = os.path.exists("main.exe")
    if not is_main_exe and not os.path.exists(main_script):
        print("\033[91m[错误] 找不到 main.py 或 main.exe！请确保它们在同一个文件夹里。\033[0m")
        os.system("pause")
        return

    print(f"\033[96m======================================================\033[0m")
    print(f"\033[96m  NX-HLB ({VERSION}) 守护进程已启动\033[0m")
    print(f"\033[96m  日志目录: {DATA_DIR}\033[0m")
    print(f"\033[96m======================================================\033[0m")
    print(f"\033[93m[提示] 如果主窗口要求输入密码，请直接在主窗口中输入。\033[0m\n")

    log_proc = None
    main_proc = None

    try:
        # 准备启动命令
        if is_main_exe:
            cmd = ["main.exe"]
        else:
            cmd = [py_exe, main_script]
            
        # 3. 拉起日志监控窗口
        log_proc = subprocess.Popen(cmd + ["--log-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)
        time.sleep(0.3) # 给日志窗口一点缓冲时间
        
        # 4. 拉起主界面窗口（包含密码鉴权）
        main_proc = subprocess.Popen(cmd + ["--main-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)

        print(f"\033[92m[OK] 主界面已拉起 (PID: {main_proc.pid})，正在静默监控...\033[0m")
        print(f"     (如需强制关闭所有程序，请在此终端按 Ctrl+C)")
        
        # ================= 5. 静默监控循环 =================
        while True:
            time.sleep(1)
            if main_proc.poll() is not None:
                exit_code = main_proc.returncode
                
                # 无论主程序怎么退出的，先清理日志窗口
                cleanup_process(log_proc, "日志监控窗口")
                
                if exit_code != 0:
                    # 崩溃退出
                    os.system("title ⚠️ NX-HLB 崩溃报告 ⚠️")
                    os.system("color 0C")
                    print("\n" * 2)
                    print("=" * 60)
                    print(f"  NX-HLB ({VERSION}) 主程序遭遇致命错误退出！(Code: {exit_code})")
                    print("=" * 60 + "\n")
                    print("--- 最后 20 行运行日志 ---")
                    
                    try:
                        time.sleep(0.5) # 等主程序彻底释放文件句柄
                        with open(log_file, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                            if not lines:
                                print("(日志文件为空)")
                            else:
                                for line in lines[-20:]:
                                    print(line, end="")
                    except Exception as e:
                        print(f"(无法读取日志文件: {e})")
                        
                    print("\n" + "=" * 60)
                    print("  请截图上方日志并向原作者反馈。按 [回车] 键退出...")
                    print("=" * 60)
                    input()
                else:
                    # 正常退出
                    print("\n\033[93m[INFO] 主程序已正常退出，守护进程结束。\033[0m")
                break
                
    except KeyboardInterrupt:
        # 捕获 Ctrl+C，安全清理
        print("\n\n\033[93m[!] 收到强制中断信号 (Ctrl+C)，正在清理所有子进程...\033[0m")
        cleanup_process(log_proc, "日志监控窗口")
        cleanup_process(main_proc, "主程序窗口")
        print("\033[92m[OK] 所有进程已安全回收。\033[0m")
        sys.exit(0)
    except Exception as e:
        print(f"\n\033[91m[致命错误] 守护进程启动失败: {e}\033[0m")
        cleanup_process(log_proc, "日志监控窗口")
        cleanup_process(main_proc, "主程序窗口")
        os.system("pause")

if __name__ == "__main__":
    main()