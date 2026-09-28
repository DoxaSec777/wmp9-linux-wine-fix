// Report WMP9's classification, dimensions, and video-related metadata.
// Pass one media URL or Windows path as the first cscript argument.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var player = new ActiveXObject("WMPlayer.OCX.7");
player.settings.autoStart = false;
player.URL = args.Item(0);
WScript.Sleep(2500);
player.controls.play();
WScript.Sleep(2500);
var media = player.currentMedia;

// Isolate unsupported COM properties so one failure does not hide other data.
function report(label, getter) {
    try { WScript.Echo(label + "=" + getter()); }
    catch (e) { WScript.Echo(label + "=<ERROR:" + e.number + ">"); }
}

report("URL", function () { return media.sourceURL; });
report("TYPE", function () { return media.getItemInfo("MediaType"); });
report("WIDTH", function () { return media.imageSourceWidth; });
report("HEIGHT", function () { return media.imageSourceHeight; });
report("DURATION", function () { return media.duration; });
report("CAN_FULLSCREEN", function () { return player.isAvailable("fullScreen"); });
report("CAN_STRETCH", function () { return player.isAvailable("stretchToFit"); });
report("ATTR_COUNT", function () { return media.attributeCount; });
try {
    for (var i = 0; i < media.attributeCount; ++i) {
        var name = media.getAttributeName(i);
        var lower = name.toLowerCase();
        if (lower.indexOf("video") >= 0 || lower.indexOf("width") >= 0 ||
                lower.indexOf("height") >= 0 || lower.indexOf("media") >= 0) {
            WScript.Echo("ATTR[" + name + "]=" + media.getItemInfo(name));
        }
    }
} catch (e2) {
    WScript.Echo("ATTR_ERROR=" + e2.number);
}
player.controls.stop();
