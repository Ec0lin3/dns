﻿Set-ExecutionPolicy -Scope LocalMachine -ExecutionPolicy Unrestricted -Force;
Register-ScheduledTask -TaskName "TASKNAME" -Trigger (New-ScheduledTaskTrigger -AtLogon) -Action (New-ScheduledTaskAction -Execute "${Env:WinDir}\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-WindowStyle Hidden -Command `"& 'C:\temp\pop.ps1'`"") -RunLevel Highest -Force;
