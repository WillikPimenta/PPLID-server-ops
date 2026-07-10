Set sh = CreateObject("Wscript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\PPLID\ops\start_ops_console.ps1"" -RepoDir ""C:\PPLID\repos\PPLID_DEV"""
sh.Run cmd, 0, False