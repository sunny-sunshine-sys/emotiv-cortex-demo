Option Explicit
Dim shell, fso, root, launcher, code, failure
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = fso.BuildPath(root, "desktop\launcher.py")
shell.CurrentDirectory = root
On Error Resume Next
code = shell.Run("pyw.exe -3 " & Chr(34) & launcher & Chr(34), 0, True)
failure = Err.Number
Err.Clear
If failure <> 0 Then
    code = shell.Run("pythonw.exe " & Chr(34) & launcher & Chr(34), 0, True)
    failure = Err.Number
    Err.Clear
End If
On Error GoTo 0
If failure <> 0 Or code <> 0 Then
    MsgBox "Cannot launch EEG Monitor. Please install Python 3.10 or newer from python.org, including Tcl/Tk and the Python launcher. Extract the complete ZIP before starting. See README.md for details.", vbExclamation, "EEG Monitor"
End If
