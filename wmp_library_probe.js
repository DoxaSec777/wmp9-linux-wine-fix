// Demonstrate the compressed-library failure mode that the mirror works around.
// The first compressed item must advance continuously with no reported errors.

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
    var lower = url.toLowerCase();
    if (lower.match(/\.(mp3|flac|ogg|m4a|aac|wma)$/)) {
        media = item;
        WScript.Echo(
            "INDEX=" + i + " URL=" + url +
            " NAME=" + item.name + " TYPE=" + item.mediaType
        );
        break;
    }
}
if (media == null) {
    WScript.Echo("NO_COMPRESSED_LIBRARY_ITEM");
    WScript.Quit(2);
}

player.currentMedia = media;
player.controls.play();
WScript.Sleep(4500);
var position1 = player.controls.currentPosition;
var duration = 0;
try { duration = player.currentMedia.duration; } catch (e2) {}
var state1 = player.playState;
var errors = 0;
try { errors = player.error.errorCount; } catch (e3) {}
WScript.Sleep(1500);
var position2 = player.controls.currentPosition;
var state2 = player.playState;
WScript.Echo(
    "DURATION=" + duration + " POS1=" + position1 + " POS2=" + position2 +
    " STATE1=" + state1 + " STATE2=" + state2 + " ERRORS=" + errors
);
player.controls.stop();

var ok = duration > 0 && position1 > 0.3 && position2 > position1 + 0.5 &&
    state1 == 3 && state2 == 3 && errors == 0;
WScript.Quit(ok ? 0 : 1);
