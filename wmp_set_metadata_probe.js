// Test which WMP9 metadata fields can be written for one temporary record.
// The generic labels avoid embedding workstation- or operator-specific names.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var player = new ActiveXObject("WMPlayer.OCX.7");
var item = player.mediaCollection.add(args.Item(0));
WScript.Sleep(800);
var values = [
    ["Title", "Compatibility Test Title"],
    ["Author", "Compatibility Test Artist"],
    ["WM/AlbumTitle", "Compatibility Test Album"],
    ["WM/Genre", "Compatibility Test Genre"],
    ["WM/TrackNumber", "7"]
];
for (var i = 0; i < values.length; i++) {
    try {
        item.setItemInfo(values[i][0], values[i][1]);
        WScript.Echo("SET_OK " + values[i][0]);
    } catch (e1) {
        WScript.Echo("SET_FAIL " + values[i][0] + " " + e1.message);
    }
}
WScript.Sleep(800);
for (var j = 0; j < values.length; j++) {
    var actual = "";
    try { actual = item.getItemInfo(values[j][0]); } catch (e2) {}
    WScript.Echo("GET " + values[j][0] + "=" + actual);
}
try { player.mediaCollection.remove(item, false); } catch (e3) {}
