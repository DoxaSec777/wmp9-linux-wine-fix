// Reconcile WMP9 MediaCollection records with a UTF-16 tab-separated plan.
// The Python synchronizer uses the safer add/update-only helper by default;
// this maintenance helper retains the original removal behavior for manual use.

var args = WScript.Arguments;
if (args.length < 1) WScript.Quit(2);

var planPath = args.Item(0);
var fso = new ActiveXObject("Scripting.FileSystemObject");
var input = fso.OpenTextFile(planPath, 1, false, -1);
var rows = [];
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

function setValue(item, name, value) {
    if (!value) return;
    try { item.setItemInfo(name, value); } catch (e) {}
}

// Resolve the Wine account at runtime instead of embedding a developer's user
// name. If neither API is available, stale-item removal is disabled safely.
function currentWindowsUser() {
    try {
        var network = new ActiveXObject("WScript.Network");
        if (network.UserName) return String(network.UserName);
    } catch (e1) {}
    try {
        var shell = new ActiveXObject("WScript.Shell");
        var name = shell.Environment("PROCESS")("USERNAME");
        if (name) return String(name);
    } catch (e2) {}
    return "";
}

var player = new ActiveXObject("WMPlayer.OCX.7");
var collection = player.mediaCollection;
var all = collection.getAll();
var existing = {};
var duplicates = [];
for (var i = 0; i < all.count; i++) {
    var media = all.item(i);
    var url = "";
    try { url = media.sourceURL; } catch (e3) {}
    if (!url) continue;
    var key = keyFor(url);
    if (existing[key] != null) duplicates.push(media);
    else existing[key] = media;
}

var active = {};
var added = 0;
var updated = 0;
for (var j = 0; j < rows.length; j++) {
    var row = rows[j];
    var rowKey = keyFor(row.url);
    var item = existing[rowKey];
    if (item == null) {
        try {
            item = collection.add(row.url);
            added++;
            WScript.Sleep(10);
        } catch (e4) {
            WScript.Echo("ADD_FAIL=" + row.url + " ERROR=" + e4.message);
            continue;
        }
    }
    active[rowKey] = true;
    setValue(item, "Title", row.title);
    setValue(item, "Author", row.author);
    setValue(item, "WM/AlbumTitle", row.album);
    setValue(item, "WM/AlbumArtist", row.author);
    setValue(item, "WM/Genre", row.genre);
    setValue(item, "WM/TrackNumber", row.track);
    setValue(item, "WM/Year", row.year);
    updated++;
}

var removed = 0;
var wineUser = currentWindowsUser();
var musicPrefix = wineUser ? "c:\\users\\" + wineUser.toLowerCase() + "\\music\\" : "";
var managedAudio = /\.(mp3|flac|m4a|aac|ogg|oga|opus|wma|wav|wave)$/i;
for (var k = 0; k < all.count; k++) {
    var oldItem = all.item(k);
    var oldUrl = "";
    try { oldUrl = oldItem.sourceURL; } catch (e5) {}
    var oldLower = String(oldUrl).toLowerCase();
    if (!musicPrefix || oldLower.indexOf(musicPrefix) != 0 || !managedAudio.test(oldLower)) continue;
    if (active[keyFor(oldUrl)]) continue;
    try {
        collection.remove(oldItem, false);
        removed++;
    } catch (e6) {
        WScript.Echo("REMOVE_FAIL=" + oldUrl + " ERROR=" + e6.message);
    }
}
for (var d = 0; d < duplicates.length; d++) {
    try {
        collection.remove(duplicates[d], false);
        removed++;
    } catch (e7) {}
}

WScript.Echo("PLANNED=" + rows.length + " ADDED=" + added + " UPDATED=" + updated + " REMOVED=" + removed);
