// Add or update WMP9 library records from a UTF-16 tab-separated plan.
// This production helper intentionally does not mass-remove records because
// repeated MediaCollection removals can destabilize WMP9 under Wine.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var fso = new ActiveXObject("Scripting.FileSystemObject");
var input = fso.OpenTextFile(args.Item(0), 1, false, -1);
var rows = [];

// The first line contains field names written by wmp9-library-sync.
if (!input.AtEndOfStream) input.ReadLine();
while (!input.AtEndOfStream) {
    var line = input.ReadLine();
    if (!line) continue;
    var fields = line.split("\t");
    while (fields.length < 7) fields.push("");
    rows.push({
        url: fields[0],
        title: fields[1],
        author: fields[2],
        album: fields[3],
        genre: fields[4],
        track: fields[5],
        year: fields[6]
    });
}
input.Close();

// Prefixing avoids collisions with inherited Object property names.
function keyFor(url) {
    return "$" + String(url).toLowerCase();
}

// Some metadata fields are read-only for particular formats, so updates are
// best effort and one rejected field must not abort the complete import.
function setValue(item, name, value) {
    if (!value) return;
    try { item.setItemInfo(name, value); } catch (e) {}
}

var player = new ActiveXObject("WMPlayer.OCX.7");
WScript.Sleep(1000);
var collection = player.mediaCollection;
var all = collection.getAll();
var existing = {};
for (var i = 0; i < all.count; i++) {
    var item = all.item(i);
    try { existing[keyFor(item.sourceURL)] = item; } catch (e1) {}
}

var added = 0;
var updated = 0;
for (var j = 0; j < rows.length; j++) {
    var row = rows[j];
    var media = existing[keyFor(row.url)];
    if (media == null) {
        try {
            media = collection.add(row.url);
            existing[keyFor(row.url)] = media;
            added++;
            // Give WMP's database time to settle before setting metadata.
            WScript.Sleep(800);
        } catch (e2) {
            WScript.Echo("ADD_FAIL=" + row.url + " ERROR=" + e2.message);
            continue;
        }
    }
    setValue(media, "Title", row.title);
    setValue(media, "Author", row.author);
    setValue(media, "WM/AlbumTitle", row.album);
    setValue(media, "WM/AlbumArtist", row.author);
    setValue(media, "WM/Genre", row.genre);
    setValue(media, "WM/TrackNumber", row.track);
    setValue(media, "WM/Year", row.year);
    updated++;
    if ((j + 1) % 25 == 0) WScript.Echo("PROGRESS=" + (j + 1));
}

WScript.Echo("PLANNED=" + rows.length + " ADDED=" + added + " UPDATED=" + updated);
