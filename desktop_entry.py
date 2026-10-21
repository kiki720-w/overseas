"""Windowed entry; the local service keeps normal pipes for isolated workers."""
import ctypes
import datetime
from pathlib import Path
import secrets
import subprocess
import sys


def main():
    directory=Path(sys.executable).resolve().parent
    log_path=directory/'diagnostics'/('startup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.log')
    try:
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open('ab') as log:
            process=subprocess.Popen([str(directory/'OverseasService.exe'),*sys.argv[1:]],
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                cwd=directory,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            result=process.wait(timeout=1.2)
        except subprocess.TimeoutExpired:
            return 0
        if result:
            ctypes.windll.user32.MessageBoxW(None,'Overseas 未能启动。请查看诊断日志：\n'+str(log_path),'Overseas',0x10)
        return result
    except OSError:
        ctypes.windll.user32.MessageBoxW(None,'无法启动 Overseas。请保留完整程序目录，并确认应用目录可以写入诊断日志。','Overseas',0x10)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
