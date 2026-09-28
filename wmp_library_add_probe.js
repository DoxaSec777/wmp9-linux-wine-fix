// Add one media item, verify playback and seeking, then remove the test record.
// Run with cscript.exe inside the target Wine prefix and pass a Windows path.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var player = new ActiveXObject("WMPlayer.OCX.7");
player.settings.autoStart = false;
player.settings.volume = 0;
var item = player.mediaCollection.add(args.Item(0));
WScript.Sleep(1200);

// Metadata support varies by format and WMP provider, so failed reads are empty.
function attribute(name) {
    try { return item.getItemInfo(name); } catch (e) { return ""; }
}

WScript.Echo("URL=" + item.sourceURL);
WScript.Echo(
    "NAME=" + item.name +
    " TITLE=" + attribute("Title") +
    " AUTHOR=" + attribute("Author") +
    " ALBUM=" + attribute("WM/AlbumTitle") +
    " MEDIATYPE=" + attribute("MediaType") +
    " FILETYPE=" + attribute("FileType")
);

player.currentMedia = item;
player.controls.play();
WScript.Sleep(3000);
var duration = player.currentMedia.duration;
var position1 = player.controls.currentPosition;
var target = duration * 0.30;
player.controls.currentPosition = target;
WScript.Sleep(1200);
var position2 = player.controls.currentPosition;
WScript.Sleep(1200);
var position3 = player.controls.currentPosition;
var state = player.playState;
WScript.Echo(
    "DURATION=" + duration +
    " POS1=" + position1 +
    " TARGET=" + target +
    " POS2=" + position2 +
    " POS3=" + position3 +
    " STATE=" + state
);
player.controls.stop();
try { player.mediaCollection.remove(item, false); }
catch (removeError) { WScript.Echo("REMOVE_ERROR=" + removeError.message); }

var ok = duration > 0 && position1 > 0.3 && position2 >= target - 2 &&
    position3 > position2 + 0.5 && state == 3;
WScript.Quit(ok ? 0 : 1);
