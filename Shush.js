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
    return Math.floor((startTime-referenceDate)/(1000*60*60*24));
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
        player.setAttribute("src", "./Audio/" + filename);
        player.currentTime = start;
        player.addEventListener('timeupdate', StopTime);
    }
    player.setAttribute("src", "./Audio/" + filename);
    player.play();
}

function StopTime() {
    let stopTime = parseInt(this.getAttribute("data-endtime"));
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
    SetMode(1);
    var player = ReadingPlayer();
    player.setAttribute("src", null);
    var audio = ShushPlayer();
    audio.play();
    audio.addEventListener('timeupdate', function(){
        var buffer = .75;
        if(this.currentTime > this.duration - buffer){
            this.currentTime = .75;
            this.play();
        }
    });
    audio = ShushPlayer2();
    audio.play();
    audio.addEventListener('timeupdate', function(){
        var buffer = 1;
        if(this.currentTime > this.duration - buffer){
            this.currentTime = 1;
            this.play();
        }
    });
}

function StopShush() {
    var audio = ShushPlayer();
    audio.pause();
    audio = ShushPlayer2();
    audio.pause();
    UpdateSnoozeTime();
    EnableButtons();
}

function StopPlayer() {
    let player = ReadingPlayer();
    player.pause;
    player.setAttribute("src", null);
    EnableButtons();
    SetMode(0);
    return;
}

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

function ShushPlayer2() {
    return document.getElementById("player3");
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