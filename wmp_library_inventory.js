// Export WMP9 Media Library records as a UTF-16 tab-separated inventory.
// Pass the destination Windows path as the first cscript argument.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var player = new ActiveXObject("WMPlayer.OCX.7");
var all = player.mediaCollection.getAll();
var fso = new ActiveXObject("Scripting.FileSystemObject");
var output = fso.CreateTextFile(args.Item(0), true, true);

// Keep every record on one TSV line even when source metadata contains controls.
function clean(value) {
    if (value == null) return "";
    return String(value).replace(/[\t\r\n]/g, " ");
}

function attribute(item, name) {
    try { return item.getItemInfo(name); } catch (e) { return ""; }
}

output.WriteLine("source\ttitle\tauthor\talbum\tgenre\ttrack\tduration\tmedia_type");
for (var i = 0; i < all.count; i++) {
    var item = all.item(i);
    var duration = 0;
    try { duration = item.duration; } catch (e2) {}
    output.WriteLine(
        clean(item.sourceURL) + "\t" +
        clean(attribute(item, "Title")) + "\t" +
        clean(attribute(item, "Author")) + "\t" +
        clean(attribute(item, "WM/AlbumTitle")) + "\t" +
        clean(attribute(item, "WM/Genre")) + "\t" +
        clean(attribute(item, "WM/TrackNumber")) + "\t" +
        clean(duration) + "\t" +
        clean(attribute(item, "MediaType"))
    );
}
output.Close();
WScript.Echo("COUNT=" + all.count);
