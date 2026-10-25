import subprocess
import sys
import os
import time
import datetime
import platform

VERSION = "v0.1.3"

# 判断当前是否处于打包后的 EXE 环境
IS_FROZEN = getattr(sys, 'frozen', False)

def main():
    if not os.path.exists("log"):
        os.makedirs("log")
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.abspath(os.path.join("log", f"NX-HLB_{VERSION}_{timestamp}.log"))
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [BOOT] Run 总控台启动，初始化日志文件。\n")
        f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [BOOT] 当前运行环境: {'EXE' if IS_FROZEN else 'Python 源码'}。\n")

    py_exe = sys.executable
    main_script = os.path.abspath("main.py")
    
    # 如果是EXE环境，拉取main.exe；如果是源码环境，拉取main.py
    if IS_FROZEN and os.path.exists("main.exe"):
        cmd = ["main.exe"]
    elif not IS_FROZEN and os.path.exists(main_script):
        cmd = [py_exe, main_script]
    else:
        print("错误：找不到对应的 main 文件，请确保目录完整！")
        os.system("pause")
        return

    try:
        # 拉起日志和主窗口
        log_proc = subprocess.Popen(cmd + ["--log-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)
        main_proc = subprocess.Popen(cmd + ["--main-window", log_file], creationflags=subprocess.CREATE_NEW_CONSOLE)

        # 静默监控
        while True:
            time.sleep(1)
            if main_proc.poll() is not None:
                exit_code = main_proc.returncode
                if exit_code != 0:
                    log_proc.terminate()
                    os.system("title ⚠️ NX-HLB 崩溃报告 ⚠️")
                    os.system("color 0C")
                    print("\n" * 2)
                    print("=" * 60)
                    print(f"  NX-HLB ({VERSION}) 主程序遭遇致命错误，已强制关闭！")
                    print("=" * 60 + "\n")
                    print("--- 最后 20 行日志 ---")
                    try:
                        with open(log_file, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                            for line in lines[-20:]:
                                print(line, end="")
                    except:
                        pass
                    print("\n" + "=" * 60)
                    print("  请截图此窗口反馈给开发者。按 [回车] 退出...")
                    print("=" * 60)
                    input()
                else:
                    log_proc.terminate()
                break
    except Exception as e:
        print(f"启动失败: {e}")
        os.system("pause")

if __name__ == "__main__":
    main()