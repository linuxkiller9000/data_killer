import tkinter as tk
from tkinter import messagebox, ttk
import subprocess
import threading
import os
import sys
import shutil
import logging

# Debug log file
LOGPATH = '/tmp/data_killer_debug.log'
logging.basicConfig(filename=LOGPATH, level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')

# --- THEMING ---
BG_COLOR = "#222831"
FG_COLOR = "#eeeeee"
BTN_BG = "#393e46"
BTN_FG = "#00adb5"
WARN_COLOR = "#ff2e63"
SUCCESS_COLOR = "#32e875"

# Global sudo password
SUDO_PASSWORD = None

# Formatting state
formatting_state = {'running': False, 'percent': 0}

# -------------------- Functions --------------------

def get_sudo_password_gui(parent=None):
    parent_widget = parent if parent else tk._get_default_root()
    if not parent_widget:
        tmp_root = tk.Tk()
        tmp_root.withdraw()
        parent_widget = tmp_root

    pw_result = {'value': None}
    dlg = tk.Toplevel(parent_widget)
    dlg.title('Authentication')
    dlg.transient(parent_widget)
    dlg.attributes('-topmost', True)
    dlg.resizable(False, False)

    tk.Label(dlg, text='Enter your sudo password:').grid(row=0, column=0, columnspan=2, padx=8, pady=(8, 0))
    entry = tk.Entry(dlg, show='*', width=36)
    entry.grid(row=1, column=0, columnspan=2, padx=8, pady=8)

    show_var = tk.IntVar(value=0)
    def toggle_show(): entry.config(show='' if show_var.get() else '*')
    cb = tk.Checkbutton(dlg, text='Show password', variable=show_var, command=toggle_show)
    cb.grid(row=2, column=0, sticky='w', padx=8, pady=(0, 8))

    def on_ok(): pw_result['value'] = entry.get(); dlg.destroy()
    def on_cancel(): pw_result['value'] = None; dlg.destroy()

    tk.Button(dlg, text='OK', width=10, command=on_ok).grid(row=3, column=0, padx=(8,4), pady=(0,8))
    tk.Button(dlg, text='Cancel', width=10, command=on_cancel).grid(row=3, column=1, padx=(4,8), pady=(0,8))

    entry.focus_set()
    dlg.grab_set()
    parent_widget.wait_window(dlg)

    return pw_result['value']

def run_with_sudo(cmd, password=None, **kwargs):
    global SUDO_PASSWORD
    if cmd[0] == 'sudo':
        cmd = list(cmd)
        if '-S' not in cmd: cmd.insert(1, '-S')
        pw = password
        if not pw: raise RuntimeError("Sudo password required")
        return subprocess.run(cmd, input=pw + '\n', text=True, check=True, capture_output=True, **kwargs)
    else:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)

def get_lsblk_info():
    import json
    lsblk_output = subprocess.check_output(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,MODEL"], encoding="utf-8")
    info = []
    data = json.loads(lsblk_output)
    for blk in data["blockdevices"]:
        if blk["type"] == "disk" and not blk["name"].startswith("sr"):
            info.append({
                "dev": f"/dev/{blk['name']}",
                "size": blk.get("size", ""),
                "mountpoint": blk.get("mountpoint", ""),
                "model": blk.get("model", "USB Disk")
            })
    return info

def list_usb_drives():
    devices = []
    by_id = '/dev/disk/by-id'
    lsblk_drives = {d["dev"]: d for d in get_lsblk_info()}
    if os.path.exists(by_id):
        for dev in os.listdir(by_id):
            if 'usb-' in dev and not dev.endswith('part'):
                real_path = os.path.realpath(os.path.join(by_id, dev))
                if real_path in lsblk_drives:
                    d = lsblk_drives[real_path]
                    desc = f"{d['dev']} | {d['size']} | {d['model']}"
                    devices.append({"dev": d['dev'], "desc": desc})
    return devices

def ensure_deps():
    missing = []
    if not shutil.which('mkfs.exfat'): missing.append('exfatprogs')
    if not shutil.which('mkfs.fat'): missing.append('dosfstools')
    if missing:
        ans = messagebox.askyesno("Missing dependencies",
                                  f"Missing tools: {', '.join(missing)}. Install now?")
        if ans:
            try: run_with_sudo(['sudo','apt','install','-y']+missing)
            except: messagebox.showerror("Error","Failed to install"); sys.exit(1)
        else: messagebox.showerror("Missing tools","Cannot continue"); sys.exit(1)

def unmount_drive(dev):
    base_name = os.path.basename(dev)
    for part in os.listdir('/dev'):
        if part.startswith(base_name):
            try: run_with_sudo(['sudo','umount', f'/dev/{part}'], check=False, capture_output=True)
            except: pass

def format_drive(drive, fs_type, password):
    try:
        unmount_drive(drive)
        if fs_type == "FAT32": cmd = ['mkfs.fat', '-F', '32', drive]
        elif fs_type == "exFAT": cmd = ['mkfs.exfat', '-n', "DATA_KILLER", drive]
        else: raise ValueError("Unsupported FS")

        run_with_sudo(['sudo'] + cmd, password=password)

        formatting_state['percent'] = 100
        formatting_state['running'] = False
        progress_bar['value'] = 100
        percent_label.config(text="100%")
        status_bar.config(text=f"Success: {drive} formatted as {fs_type}", bg=SUCCESS_COLOR)
        messagebox.showinfo("Success", f"{drive} formatted as {fs_type}")
    except Exception as e:
        formatting_state['running'] = False
        formatting_state['percent'] = 0
        progress_bar['value'] = 0
        percent_label.config(text="0%")
        status_bar.config(text="Error formatting", bg=WARN_COLOR)
        messagebox.showerror("Error", f"Failed to format: {e}")

def format_async(drive, fs_type, password):
    format_drive(drive, fs_type, password)

def start_progress_updater():
    progress_bar.config(mode='determinate', maximum=100)
    def updater():
        if formatting_state.get('running'):
            p = formatting_state.get('percent',0)
            if p < 95:
                inc = max(1,int((95-p)/8) or 1)
                p = min(95,p+inc)
                formatting_state['percent'] = p
            progress_bar['value'] = formatting_state['percent']
            percent_label.config(text=f"{formatting_state['percent']}%")
            root.after(300, updater)
        else:
            progress_bar['value'] = formatting_state.get('percent',100)
            percent_label.config(text=f"{formatting_state.get('percent',100)}%")
    root.after(100, updater)

def on_format():
    sel = drive_var.get()
    if not sel: messagebox.showwarning("Missing Info","Select USB"); return
    sel_dev = sel.split()[0]
    fs_type = fs_var.get()
    if not fs_type: messagebox.showwarning("Missing Info","Select FS"); return
    if sel_dev == "/dev/sda": messagebox.showerror("Danger","Cannot format main drive!"); return

    c1 = messagebox.askokcancel(
        "Confirm Format",
        f"Format {sel_dev} as {fs_type}? This will ERASE ALL data!\nIf you continue you agree to delete all your data"
    )
    if not c1: return

    formatting_state['percent'] = 0
    formatting_state['running'] = True
    progress_bar['value'] = 0
    percent_label.config(text="0%")

    password = get_sudo_password_gui(root)
    if not password:
        formatting_state['running'] = False
        status_bar.config(text="Cancelled")
        return

    status_bar.config(text="Formatting...", bg=BTN_BG)
    t = threading.Thread(target=format_async, args=(sel_dev, fs_type, password), daemon=True)
    t.start()
    start_progress_updater()

# -------------------- GUI --------------------
root = tk.Tk()
root.title("Data_killer - USB Formatter")
root.configure(bg=BG_COLOR)
ensure_deps()

# ---- Logo ----
LOGO_PATH = "data_killer/logo.png"  # ضع هنا مسار اللوجو
if os.path.exists(LOGO_PATH):
    try:
        logo_img = tk.PhotoImage(file=LOGO_PATH)
        tk.Label(root, image=logo_img, bg=BG_COLOR).pack(pady=(10,0))
    except:
        tk.Label(root, text="(Logo error)", fg=FG_COLOR, bg=BG_COLOR).pack(pady=(10,0))
else:
    tk.Label(root, text="Data_killer", font=("Arial",18,"bold"), fg=BTN_FG, bg=BG_COLOR).pack(pady=(10,0))

tk.Label(root, text="USB Device: (Device | Size | Model)", font=("Arial",12), fg=FG_COLOR, bg=BG_COLOR).pack(pady=5)
devices = list_usb_drives()
drive_var = tk.StringVar()
ttk.Combobox(root, textvariable=drive_var, values=[d["desc"] for d in devices], state="readonly", width=60).pack()

tk.Label(root, text="File System:", font=("Arial",12), fg=FG_COLOR, bg=BG_COLOR).pack(pady=5)
fs_var = tk.StringVar()
ttk.Combobox(root, textvariable=fs_var, values=["FAT32","exFAT"], state="readonly").pack()

progress_frame = tk.Frame(root, bg=BG_COLOR)
progress_frame.pack(pady=(10,0))
progress_bar = ttk.Progressbar(progress_frame, length=360, mode='determinate')
progress_bar.pack(side='left')
percent_label = tk.Label(progress_frame, text="0%", fg=FG_COLOR, bg=BG_COLOR, font=("Arial",10,"bold"))
percent_label.pack(side='left', padx=(8,0))

btn_frame = tk.Frame(root, bg=BG_COLOR)
btn_frame.pack(pady=15)
tk.Button(btn_frame,text="Format",command=on_format,bg=BTN_BG,fg=BTN_FG,font=("Arial",12)).pack(side='left', padx=10)

status_bar = tk.Label(root,text="Ready",bd=1,relief="sunken",anchor="w",font=("Arial",10),fg=FG_COLOR,bg=BTN_BG)
status_bar.pack(fill="x",side="bottom", pady=(8,0))

root.mainloop()
