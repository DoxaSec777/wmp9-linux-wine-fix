// Play the first mirrored WAV item from WMP9's actual Media Library.
// Success requires duration, advancing playback, seeking, and zero WMP errors.

var player = new ActiveXObject("WMPlayer.OCX.7");
player.settings.autoStart = false;
player.settings.volume = 0;
var all = player.mediaCollection.getAll();
WScript.Echo("COUNT=" + all.count);

var media = null;
for (var i = 0; i < all.count; i++) {
    var item = all.item(i);
    var url = "";
    try { url = item.sourceURL; } catch (e1) {}
    if (url.toLowerCase().match(/\.wav$/)) {
        media = item;
        WScript.Echo(
            "INDEX=" + i + " URL=" + url + " NAME=" + item.name +
            " TITLE=" + item.getItemInfo("Title") +
            " AUTHOR=" + item.getItemInfo("Author")
        );
        break;
    }
}
if (media == null) {
    WScript.Echo("NO_COMPAT_LIBRARY_ITEM");
    WScript.Quit(2);
}

player.currentMedia = media;
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
var errors = 0;
try { errors = player.error.errorCount; } catch (e2) {}
WScript.Echo(
    "DURATION=" + duration + " POS1=" + position1 + " TARGET=" + target +
    " POS2=" + position2 + " POS3=" + position3 +
    " STATE=" + state + " ERRORS=" + errors
);
player.controls.stop();

var ok = duration > 0 && position1 > 0.3 && position2 >= target - 2 &&
    position3 > position2 + 0.5 && state == 3 && errors == 0;
WScript.Quit(ok ? 0 : 1);
