import subprocess
import sys
import os
import time
import datetime
import platform

VERSION = "v0.2.3"
DATA_DIR = "NX-HLB_Data"

def cleanup_process(proc):
    """彻底清理子进程（防僵尸进程）"""
    if proc and proc.poll() is None:
        try:
            if platform.system() == "Windows":
                # Windows 下强制杀死进程树
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        except:
            pass

def main():
    # 1. 确保数据文件夹存在
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    # 2. 生成带时间戳的日志文件 (放在数据文件夹里)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.abspath(os.path.join(DATA_DIR, f"NX-HLB_Log_{timestamp}.log"))
    
    # 初始化日志
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [BOOT] Run 守护进程启动，日志初始化。\n")

    py_exe = sys.executable
    main_script = os.path.abspath("main.py")
    
    # 兼容打包后的 EXE 环境
    is_main_exe = os.path.exists("main.exe")
    if not is_main_exe and not os.path.exists(main_script):
        print("\033[91m错误：找不到 main.py 或 main.exe！\033[0m")
        os.system("pause")
        return

    print(f"\033[96m[NX-HLB] 守护进程启动中...\033[0m")
    print(f"日志文件: {log_file}")

    log_proc = None
    main_proc = None

    try:
        # 启动主界面和日志监控
        if is_main_exe:
            cmd = ["main.exe"]
        else:
            cmd = [py_exe, main_script]
            
        # 拉起日志窗口
        log_proc = subprocess.Popen(cmd + ["--log-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)
        time.sleep(0.2) # 稍微等待日志窗口准备就绪
        # 拉起主界面窗口
        main_proc = subprocess.Popen(cmd + ["--main-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)

        print(f"\033[92m[NX-HLB] 界面已拉起，正在静默监控主程序...\033[0m")
        
        # ================= 静默监控循环 =================
        while True:
            time.sleep(1)
            # 检查主程序是否还活着
            if main_proc.poll() is not None:
                exit_code = main_proc.returncode
                
                # 无论主程序怎么退出的，先清理日志窗口，防止卡死
                cleanup_process(log_proc)
                
                if exit_code != 0:
                    # 非正常退出（崩溃）
                    os.system("title ⚠️ NX-HLB 崩溃报告 ⚠️")
                    os.system("color 0C")
                    print("\n" * 2)
                    print("=" * 60)
                    print(f"  NX-HLB ({VERSION}) 主程序遭遇致命错误退出！(Code: {exit_code})")
                    print("=" * 60 + "\n")
                    print("--- 最后 20 行日志 ---")
                    
                    # 尝试读取最后 20 行日志
                    try:
                        # 等待 0.5 秒确保数据刷入硬盘
                        time.sleep(0.5)
                        with open(log_file, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                            for line in lines[-20:]:
                                print(line, end="")
                    except Exception as e:
                        print(f"(无法读取日志文件: {e})")
                        
                    print("\n" + "=" * 60)
                    print("  请截图此窗口反馈给开发者。按 [回车] 退出...")
                    print("=" * 60)
                    input()
                else:
                    # 正常退出
                    print("\033[93m[NX-HLB] 主程序已正常退出，守护进程结束。\033[0m")
                break
                
    except KeyboardInterrupt:
        # 捕获 Ctrl+C，安全清理
        print("\n\033[93m[!] 收到中断信号，正在清理进程...\033[0m")
        cleanup_process(log_proc)
        cleanup_process(main_proc)
        sys.exit(0)
    except Exception as e:
        print(f"\033[91m守护进程启动失败: {e}\033[0m")
        cleanup_process(log_proc)
        cleanup_process(main_proc)
        os.system("pause")

if __name__ == "__main__":
    main()