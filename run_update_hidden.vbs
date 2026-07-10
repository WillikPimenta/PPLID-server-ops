Set sh = CreateObject("Wscript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\PPLID\ops\watch_all.ps1"""
sh.Run cmd, 0, True
