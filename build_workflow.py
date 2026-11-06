import plistlib, zipfile, os
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "GD Sync.alfredworkflow")
SF, RS = "B1B2C3D4-0000-4000-8000-000000000001", "B1B2C3D4-0000-4000-8000-000000000002"
plist = {
    "bundleid": "com.koki.gd-sync", "createdby": "Koki", "name": "GD Sync",
    "description": "git-like push / pull between this Mac and a Google Drive folder",
    "readme": "gd pull NAME | gd pull to ~/dir NAME | gd push NAME | gd ls | gd remote PATH | gd workspace DIR",
    "version": "1.0", "webaddress": "", "disabled": False, "variablesdontexport": [],
    "connections": {SF: [{"destinationuid": RS, "modifiers": 0, "modifiersubtext": "", "vitoclose": False}]},
    "objects": [
        {"uid": SF, "type": "alfred.workflow.input.scriptfilter", "version": 3, "config": {
            "alfredfiltersresults": False, "alfredfiltersresultsmatchmode": 2,
            "argumenttreatemptyqueryasnil": False, "argumenttrimmode": 0, "argumenttype": 1, "escaping": 102,
            "keyword": "gd", "queuedelaycustom": 3, "queuedelayimmediatelyinitially": True, "queuedelaymode": 0,
            "queuemode": 1, "runningsubtext": "", "script": 'python3 ./gd.py alfred "$1"', "scriptargtype": 1,
            "scriptfile": "", "subtext": "pull / push files with Google Drive", "title": "GD Sync", "type": 0,
            "withspace": True}},
        {"uid": RS, "type": "alfred.workflow.action.script", "version": 2, "config": {
            "concurrently": False, "escaping": 102, "script": 'python3 ./gd.py run "$1"', "scriptargtype": 1,
            "scriptfile": "", "type": 0}},
    ],
    "uidata": {SF: {"xpos": 40, "ypos": 40}, RS: {"xpos": 300, "ypos": 40}},
}
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("info.plist", plistlib.dumps(plist))
    z.write(os.path.join(HERE, "gd.py"), "gd.py")
print("built", OUT)
