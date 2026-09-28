-- mpv-side acknowledgement for the synchronized WMP startup gate.
--
-- playback-restart is emitted after initial playback setup and after seeks.  It
-- is stronger than process/window creation, but it is not sufficient on its
-- own: the Python service also verifies that WMP and mpv are held near zero and
-- that the mapped X11 window exactly matches the current WMP renderer geometry.
-- The marker contains diagnostic time only; its existence is the handshake.
local path = os.getenv('WMP9_FRAME_READY')
mp.register_event('playback-restart', function()
    if path and mp.get_property_bool('vo-configured', false) then
        local f = io.open(path, 'w')
        if f then
            f:write(tostring(mp.get_property_number('time-pos', -1)))
            f:close()
        end
    end
end)
