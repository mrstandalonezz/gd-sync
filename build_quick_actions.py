"""Builds Finder right-click Quick Actions (GD Push / GD Pull / GD Pull Here… / GD Backup) into ~/Library/Services."""
import os, plistlib, shutil, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/Library/Services")
GD = os.path.join(HERE, "gd.py")
ACTIONS = [  # (menu name, gd finder sub-command, accepted Finder types)
    ("GD Push", "push", ["public.item"]),
    ("GD Pull", "pull", ["public.item"]),
    ("GD Pull Here…", "pullhere", ["public.folder"]),
    ("GD Backup", "backup", ["public.item"]),
]


def wflow(sub, file_type):
    in_type = "com.apple.Automator.fileSystemObject" + (".folder" if file_type == "public.folder" else "")
    # log everything and always exit 0: a non-zero exit makes Finder show the vague
    # "There was a problem with the input to the Service" instead of our notification
    script = ('export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"\n'
              'LOG="$HOME/.config/gd/finder.log"; mkdir -p "${LOG:h}"\n'
              f'echo "--- $(date) {sub} $*" >> "$LOG"\n'
              f'if ! python3 "{GD}" finder {sub} "$@" >> "$LOG" 2>&1; then\n'
              '  osascript -e "display notification \\"see ~/.config/gd/finder.log\\" with title \\"⚠️ gd failed\\"" \n'
              'fi\n'
              'exit 0\n')
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3", "AMApplication": ["Automator"], "AMParameterProperties": {
            "COMMAND_STRING": {}, "CheckedForUserDefaultShell": {}, "inputMethod": {}, "shell": {}, "source": {}},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {"COMMAND_STRING": script, "CheckedForUserDefaultShell": True,
                             "inputMethod": 1, "shell": "/bin/zsh", "source": ""},
        "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True, "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction", "InputUUID": str(uuid.uuid4()).upper(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"], "OutputUUID": str(uuid.uuid4()).upper(),
        "UUID": str(uuid.uuid4()).upper(), "UnlocalizedApplications": ["Automator"], "conversionLabel": 0,
        "arguments": {str(i): {"default value": v, "name": n, "required": "0", "type": "0", "uuid": str(i)}
                      for i, (n, v) in enumerate([("inputMethod", 0), ("CheckedForUserDefaultShell", False),
                                                  ("source", ""), ("COMMAND_STRING", ""), ("shell", "/bin/sh")])},
        "isViewVisible": 1, "location": "300:300", "nibPath":
            "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
    }
    return {
        "AMApplicationBuild": "534", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": 1}], "connectors": {},
        "workflowMetaData": {  # same shape Automator writes for a Finder Quick Action
            "applicationBundleIDsByPath": {}, "applicationPaths": [],
            "inputTypeIdentifier": in_type, "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15, "processesInput": False,
            "serviceInputTypeIdentifier": in_type, "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False, "systemImageName": "NSActionTemplate", "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu"},
    }


os.makedirs(DEST, exist_ok=True)
for name, sub, types in ACTIONS:
    bundle = os.path.join(DEST, name + ".workflow")
    shutil.rmtree(bundle, ignore_errors=True)
    os.makedirs(os.path.join(bundle, "Contents"))
    info = {"NSServices": [{"NSMenuItem": {"default": name}, "NSMessage": "runWorkflowAsService",
                            "NSBackgroundColorName": "background", "NSIconName": "NSActionTemplate",
                            "NSSendFileTypes": types}]}
    with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    with open(os.path.join(bundle, "Contents", "document.wflow"), "wb") as f:
        plistlib.dump(wflow(sub, types[0]), f)
    print("installed", bundle)
