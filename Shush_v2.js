function Tick() {
    var mode = GetMode();
    if (mode == 1) {
        CheckAlarmTime();
        return;
    }
    if (mode == 2) {
        CheckSnoozeTime();
        return;
    }
    UpdateSnoozeTime();
}

var shushWakeLock = null;
var isShushActive = false;
var activeShushPlayer = null;
const SHUSH_SOURCE = "Pink_Noise.wav";
const SHUSH_LABEL = "Pink Noise";
var mediaSessionConfigured = false;
var shushAudioContext = null;
var shushAudioBuffer = null;
var shushBufferSource = null;
var shushBufferLoading = null;

function ConfigureShushPlayers() {
    var primary = ShushPlayer();
    if (!primary) return;
    if (primary.getAttribute('data-shush-configured')) return;
    primary.setAttribute('preload', 'auto');
    primary.setAttribute('playsinline', '1');
    primary.loop = true;
    primary.setAttribute('data-shush-configured', 'true');
}

function ensureShushAudioContext() {
    if (shushAudioContext) return true;
    var AudioContextType = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextType) return false;
    shushAudioContext = new AudioContextType();
    return true;
}

function loadShushAudioBuffer() {
    if (shushAudioBuffer) return Promise.resolve(shushAudioBuffer);
    if (shushBufferLoading) return shushBufferLoading;
    if (!ensureShushAudioContext()) {
        return Promise.reject(new Error('Web Audio API is not available.'));
    }

    shushBufferLoading = fetch(SHUSH_SOURCE)
        .then(function (response) {
            if (!response.ok) {
                throw new Error('Failed to load shush audio: ' + response.status);
            }
            return response.arrayBuffer();
        })
        .then(function (arrayBuffer) {
            return shushAudioContext.decodeAudioData(arrayBuffer);
        })
        .then(function (buffer) {
            shushAudioBuffer = buffer;
            shushBufferLoading = null;
            return buffer;
        })
        .catch(function (error) {
            shushBufferLoading = null;
            throw error;
        });

    return shushBufferLoading;
}

function startShushLoopWithAudioContext() {
    if (!shushAudioContext || !shushAudioBuffer) return;
    if (shushBufferSource) {
        try {
            shushBufferSource.stop(0);
        } catch (error) {}
        shushBufferSource = null;
    }
    if (shushAudioContext.state === 'suspended') {
        shushAudioContext.resume().catch(function () {});
    }

    var source = shushAudioContext.createBufferSource();
    source.buffer = shushAudioBuffer;
    source.loop = true;
    source.connect(shushAudioContext.destination);
    shushBufferSource = source;
    source.start(0);
}

function stopShushLoopWithAudioContext() {
    if (!shushBufferSource) return;
    try {
        shushBufferSource.stop(0);
    } catch (error) {}
    shushBufferSource = null;
}

function startShushLegacy() {
    var primary = ShushPlayer();
    primary.setAttribute('src', SHUSH_SOURCE);
    primary.load();
    primary.currentTime = 0;
    primary.pause();
    primary.loop = true;
    safePlay(primary);
    activeShushPlayer = primary;
}

function resumeShushPlayback() {
    if (shushBufferSource) {
        if (shushAudioContext && shushAudioContext.state === 'suspended') {
            shushAudioContext.resume().catch(function () {});
        }
        return;
    }
    if (activeShushPlayer) {
        safePlay(activeShushPlayer);
    }
}

function safePlay(player) {
    var result = player.play();
    if (result && (typeof result.catch === "function")) {
        result.catch(function () {});
    }
}

function CheckAlarmTime() {
    var date = new Date();
    var hour = date.getHours();
    if (hour>12) hour -= 12;
    var minute = date.getMinutes();
    var data = Data();
    var alarmHour = data.getAttribute("data-hour");
    var alarmMinute = data.getAttribute("data-minute");
    if ((hour == alarmHour) && (minute == alarmMinute)) {
        StopShush();
    }
}

function CheckSnoozeTime() {
    var date = new Date();
    var hour = date.getHours();
    if (hour>12) hour -= 12;
    var minute = date.getMinutes();
    var snoozetime = SnoozeTime();
    var alarmHour = snoozetime.getAttribute("data-hour");
    var alarmMinute = snoozetime.getAttribute("data-minute");
    if ((hour == alarmHour) && (minute == alarmMinute)) {
        StopShush();
    }
}

function UpdateSnoozeTime() {
    var data = document.getElementById("data");
    var snoozeMinutes = parseInt(data.getAttribute("data-snooze"));
    var snoozeTime = SnoozeTime();
    var date = new Date();
    date.setMinutes(Math.round((date.getMinutes() + snoozeMinutes) / 5) * 5);
    var hour = date.getHours();
    if (hour>12) hour -= 12;
    var minute = date.getMinutes();
    var minuteString = minute.toString();
    if (minuteString.length<2) {
        minuteString = '0' + minuteString;
    }
    snoozeTime.innerText = hour + ':' + minuteString;
    snoozeTime.setAttribute("data-hour", hour);
    snoozeTime.setAttribute("data-minute", minute);
}

function Alarm() {
    let mode = GetMode();
    if (mode > 0) {
        StopShush();
        SetMode(0);
        return;
    }
    let player = ReadingPlayer();
    let source = player.getAttribute("src");
    if ((source != null)  && (source != "null"))
    {
        StopPlayer();
        return;
    }
    DisableButtons();
    let day = ScheduleDay();
    PlayTrack(player, day, 0);
}

function ScheduleDay() {
    let startTime = new Date();
    //startTime.setTime(startTime.getTime() - (9*60*60*1000));
    let referenceDate = new Date(2020, 3, 5, 3, 0, 0, 0);
    return (Math.floor((startTime-referenceDate)/(1000*60*60*24))) % 366;
}

function PlayTrack(player, day, track) {
    player.removeEventListener('timeupdate', StopTime);
    if (track >= readingSchedule[day].length) 
    {
        StartShush();
        return;
    }
    let filename = readingSchedule[day][track];
    player.setAttribute("data-track", track);
    let parts = filename.split(',');
    if (parts.length > 1)
    {
        filename = parts[0];
        let start = parseInt(parts[1]);
        let endtime = parseInt(parts[2]) + start;
        player.setAttribute("data-endtime", endtime);
        player.currentTime = start;
        player.addEventListener('timeupdate', StopTime);
    }
    else {
        player.removeAttribute("data-endtime");
    }
    player.setAttribute("src", "./Audio/" + filename);
    player.play();
}

function StopTime() {
    let stopTime = parseInt(this.getAttribute("data-endtime"));
    if (isNaN(stopTime)) return;
    if(this.currentTime > stopTime){
        PlayNext();
    }
}

function PlayNext() {
    let player = ReadingPlayer();
    let track = parseInt(player.getAttribute('data-track')) + 1;
    let day = ScheduleDay();
    PlayTrack(player, day, track);
}

function AlarmNo() {
    let alarmNo = NoReading();
    if (IsDisabled(alarmNo)) return;
    DisableButtons();
    StartShush();
}

function Snooze() {
    let snooze = SnoozeButton();
    if (IsDisabled(snooze)) return;
    DisableButtons();
    StartShush();
    SetMode(2);
}

function StartShush() {
    var mode = GetMode();
    if (mode == 0) {
        SetMode(1);
    }
    ConfigureShushPlayers();
    var reading = ReadingPlayer();
    reading.removeAttribute("src");
    reading.load();

    isShushActive = true;
    activeShushPlayer = ShushPlayer();
    loadShushAudioBuffer().then(function () {
        if (!isShushActive) return;
        startShushLoopWithAudioContext();
    }).catch(function () {
        if (!isShushActive) return;
        startShushLegacy();
    });

    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'playing';
    }
    ConfigureMediaSession();
    AcquireScreenWakeLock();
}

function StopShush() {
    var audio = ShushPlayer();
    stopShushLoopWithAudioContext();
    isShushActive = false;
    audio.pause();
    audio.currentTime = 0;
    activeShushPlayer = null;
    SetMode(0);
    UpdateSnoozeTime();
    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'paused';
    }
    ReleaseScreenWakeLock();
    EnableButtons();
}

function ConfigureMediaSession() {
    if (!('mediaSession' in navigator) || mediaSessionConfigured) return;
    mediaSessionConfigured = true;

    navigator.mediaSession.metadata = new MediaMetadata({
        title: SHUSH_LABEL,
        artist: 'Shush',
        album: 'Alarm/standby loop'
    });

    navigator.mediaSession.setActionHandler('play', function () {
        if (isShushActive) {
            resumeShushPlayback();
            return;
        }
        StartShush();
    });
    navigator.mediaSession.setActionHandler('pause', function () {
        if (shushBufferSource) {
            stopShushLoopWithAudioContext();
            return;
        }
        if (activeShushPlayer) {
            activeShushPlayer.pause();
        }
    });
    navigator.mediaSession.setActionHandler('stop', function () {
        StopShush();
    });
}

function AcquireScreenWakeLock() {
    if (!('wakeLock' in navigator)) return;
    if (shushWakeLock) return;
    navigator.wakeLock.request('screen')
        .then(function (lock) {
            shushWakeLock = lock;
        })
        .catch(function () {});
}

function ReleaseScreenWakeLock() {
    if (!shushWakeLock) return;
    shushWakeLock.release()
        .then(function () {
            shushWakeLock = null;
        })
        .catch(function () {});
}

function StopPlayer() {
    let player = ReadingPlayer();
    player.pause();
    player.setAttribute("src", null);
    EnableButtons();
    SetMode(0);
    return;
}

function InitializeShushRuntime() {
    ConfigureShushPlayers();

    document.addEventListener('visibilitychange', function () {
        if (!isShushActive) return;
        if (document.visibilityState === 'visible') {
            if (!activeShushPlayer) return;
            AcquireScreenWakeLock();
            resumeShushPlayback();
        }
        else {
            ReleaseScreenWakeLock();
        }
    });
}

InitializeShushRuntime();

function OpenDropDown(element) {
    if (element.classList.contains("open")) {
        CloseDropDown(element);
        return;
    }
    element.classList.add("open");
}

function CloseDropDown(element) {
    element.classList.remove("open");
}

function HourDropdown(element) {
    var text = element.innerText;
    var hour = Hour();
    hour.innerText = text;
    var data = Data();
    data.setAttribute("data-hour", text); 
    CloseDropDown(element.parentElement);
}

function MinuteDropdown(element) {
    var text = element.innerText;
    var minute = Minute();
    minute.innerText = text;
    var data = Data();
    data.setAttribute("data-minute", text); 
    CloseDropDown(element.parentElement);
}

function EnableButtons() {
    var alarm = AlarmButton();
    var noreading = NoReading();
    var snooze = SnoozeButton();
    var snoozeUp = SnoozeUp();
    var snoozeDown = SnoozeDown();
    //var pause = PauseButton();
    alarm.innerText = "Alarm";
    Enable(noreading);
    Enable(snooze);
    Enable(snoozeUp);
    Enable(snoozeDown);
}

function DisableButtons() {
    var alarm = AlarmButton();
    var noreading = NoReading();
    var snooze = SnoozeButton();
    var snoozeUp = SnoozeUp();
    var snoozeDown = SnoozeDown();
    var player = ReadingPlayer();
    //var pause = PauseButton();
    alarm.innerText = "Stop";
    Disable(noreading);
    Disable(snooze);
    Disable(snoozeUp);
    Disable(snoozeDown);
    player.setAttribute("src", null);
}

function ChangeSnooze(amount) {
    let data = Data();
    let snoozeMinutes = parseInt(data.getAttribute("data-snooze"))+ amount;
    if (snoozeMinutes < 15) return;
    data.setAttribute("data-snooze", snoozeMinutes);
    let snoozeMinutesText = SnoozeMinutes();
    snoozeMinutesText.innerText = snoozeMinutes;
    UpdateSnoozeTime();
}

function IncrementSnooze() {
    var snoozeUp = SnoozeUp();
    if (IsDisabled(snoozeUp)) return;
    ChangeSnooze(15);
}

function DecrementSnooze() {
    var snoozeDown = SnoozeDown();
    if (IsDisabled(snoozeDown)) return;
    ChangeSnooze(-15);
}

function GetMode() {
    let data = Data();
    return data.getAttribute("data-mode");
}

function SetMode(mode) {
    let data = Data();
    data.setAttribute("data-mode", mode);
}

function Enable(element) {
    element.classList.remove("disabled");
}

function Disable(element) {
    element.classList.add("disabled");
}

function IsDisabled(element)
{
    return element.classList.contains("disabled");
}

function ReadingPlayer() {
    return document.getElementById("player");  
}

function ShushPlayer() {
    return document.getElementById("player2");
}


function AlarmButton() {
    return document.getElementById("Alarm");
}

function NoReading() {
    return document.getElementById("AlarmNo");
}

function SnoozeButton() {
    return document.getElementById("SnoozeButton");
}

function PauseButton() {
    return document.getElementById("PauseButton");
}

function Hour() {
    return document.getElementById("hour");
}

function Minute() {
    return document.getElementById("minute");
}

function SnoozeTime() {
    return document.getElementById("snoozetime");
}

function SnoozeMinutes() {
    return document.getElementById("snoozeminutes");
}
function Data() {
    return document.getElementById("data");
}

function SnoozeUp() {
    return document.getElementById("snoozeUp");
}

function SnoozeDown() {
    return document.getElementById("snoozeDown");
}

