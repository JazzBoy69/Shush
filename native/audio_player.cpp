#include <alsa/asoundlib.h>
#include <json-c/json.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <limits.h>
#include <mutex>
#include <poll.h>
#include <queue>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
constexpr char kSocket[] = "/run/bedtime-audio-player/audio.sock";
constexpr char kPinkNoise[] = "/var/lib/bedtime-audio/Pink_Noise.wav";
constexpr char kStateFile[] = "/var/lib/bedtime-audio/audio-daemon-state.json";
constexpr std::size_t kMaxRequest = 8192;
constexpr std::size_t kMaxWavBytes = 32 * 1024 * 1024;

volatile sig_atomic_t g_stop = 0;

void on_signal(int) { g_stop = 1; }

std::runtime_error error(const std::string& message) {
    return std::runtime_error(message);
}

std::string canonical_file(const std::string& value) {
    char resolved[PATH_MAX];
    if (!realpath(value.c_str(), resolved)) {
        throw error("cannot resolve audio path: " + value + ": " + std::strerror(errno));
    }
    struct stat info {};
    if (stat(resolved, &info) != 0 || !S_ISREG(info.st_mode)) {
        throw error("audio path is not a regular file: " + value);
    }
    return resolved;
}

uint16_t u16(const unsigned char* p) {
    return static_cast<uint16_t>(p[0]) | static_cast<uint16_t>(p[1] << 8);
}
uint32_t u32(const unsigned char* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

struct PcmFile {
    unsigned rate = 0;
    unsigned channels = 0;
    std::vector<int16_t> samples;
    std::size_t frames() const { return samples.size() / channels; }
};

PcmFile read_wav(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw error("cannot open WAV file: " + path);
    std::array<unsigned char, 12> header{};
    file.read(reinterpret_cast<char*>(header.data()), header.size());
    if (file.gcount() != static_cast<std::streamsize>(header.size()) ||
        std::memcmp(header.data(), "RIFF", 4) || std::memcmp(header.data() + 8, "WAVE", 4)) {
        throw error("WAV must use a RIFF/WAVE container");
    }

    bool have_fmt = false, have_data = false;
    unsigned channels = 0, rate = 0, bits = 0, format = 0;
    std::vector<unsigned char> data;
    while (file && (!have_fmt || !have_data)) {
        std::array<unsigned char, 8> chunk{};
        file.read(reinterpret_cast<char*>(chunk.data()), chunk.size());
        if (file.gcount() == 0) break;
        if (file.gcount() != static_cast<std::streamsize>(chunk.size())) throw error("truncated WAV chunk header");
        uint32_t size = u32(chunk.data() + 4);
        if (!std::memcmp(chunk.data(), "fmt ", 4)) {
            if (size < 16 || size > 4096) throw error("unsupported WAV format chunk");
            std::vector<unsigned char> fmt(size);
            file.read(reinterpret_cast<char*>(fmt.data()), size);
            if (file.gcount() != static_cast<std::streamsize>(size)) throw error("truncated WAV format chunk");
            format = u16(fmt.data());
            channels = u16(fmt.data() + 2);
            rate = u32(fmt.data() + 4);
            bits = u16(fmt.data() + 14);
            have_fmt = true;
        } else if (!std::memcmp(chunk.data(), "data", 4)) {
            if (size > kMaxWavBytes) throw error("WAV data exceeds 32 MiB limit");
            data.resize(size);
            file.read(reinterpret_cast<char*>(data.data()), size);
            if (file.gcount() != static_cast<std::streamsize>(size)) throw error("truncated WAV data chunk");
            have_data = true;
        } else {
            file.seekg(size, std::ios::cur);
            if (!file) throw error("truncated WAV chunk");
        }
        if (size & 1) file.seekg(1, std::ios::cur);
    }
    if (!have_fmt || !have_data || format != 1 || bits != 16 || channels < 1 || channels > 2 ||
        rate < 8000 || rate > 192000 || data.empty() || data.size() % (channels * 2) != 0) {
        throw error("WAV must be uncompressed 16-bit PCM, 1–2 channels, 8–192 kHz");
    }

    PcmFile result;
    result.rate = rate;
    result.channels = channels;
    result.samples.resize(data.size() / 2);
    for (std::size_t i = 0; i < result.samples.size(); ++i) {
        result.samples[i] = static_cast<int16_t>(u16(data.data() + i * 2));
    }
    return result;
}

std::string json_string(json_object* object, const char* key) {
    json_object* value = nullptr;
    if (!json_object_object_get_ex(object, key, &value) || !json_object_is_type(value, json_type_string)) {
        throw error(std::string("missing or invalid field: ") + key);
    }
    return json_object_get_string(value);
}

json_object* response(bool ok, const std::string& message = "") {
    json_object* result = json_object_new_object();
    json_object_object_add(result, "ok", json_object_new_boolean(ok));
    if (!ok) json_object_object_add(result, "error", json_object_new_string(message.c_str()));
    return result;
}

struct Event { std::string kind, path, reason, message; };

class AudioService {
public:
    ~AudioService() { shutdown(); }

    void start() {
        struct stat info {};
        if (lstat(kSocket, &info) == 0) {
            if (!S_ISSOCK(info.st_mode)) throw error("audio socket path exists and is not a socket");
            int probe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
            sockaddr_un address{};
            address.sun_family = AF_UNIX;
            std::strncpy(address.sun_path, kSocket, sizeof(address.sun_path) - 1);
            int rc = connect(probe, reinterpret_cast<sockaddr*>(&address), sizeof(address));
            int connect_error = errno;
            close(probe);
            if (rc == 0 || connect_error != ECONNREFUSED) throw error("audio socket is already active or cannot be inspected");
            unlink(kSocket);
        }
        listener_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (listener_ < 0) throw error("cannot create audio socket: " + std::string(std::strerror(errno)));
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        std::strncpy(address.sun_path, kSocket, sizeof(address.sun_path) - 1);
        if (bind(listener_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) || chmod(kSocket, 0600) || listen(listener_, 8)) {
            throw error("cannot bind audio socket: " + std::string(std::strerror(errno)));
        }
        load_saved_state();
        if (saved_pink_) {
            std::cerr << "Restoring continuous pink noise\n";
            start_noise();
            current_kind_ = "pink_noise";
        }
        std::cerr << "Native audio service ready\n";
    }

    void run() {
        while (!g_stop) {
            pollfd pfd{listener_, POLLIN, 0};
            int ready = poll(&pfd, 1, 250);
            if (ready < 0) { if (errno == EINTR) continue; throw error("audio socket poll failed"); }
            if (!ready) continue;
            int client = accept4(listener_, nullptr, nullptr, SOCK_CLOEXEC);
            if (client < 0) { if (errno == EINTR) continue; throw error("audio socket accept failed"); }
            timeval timeout{5, 0};
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
            setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
            handle(client);
            close(client);
        }
    }

    void shutdown() {
        if (listener_ >= 0) { close(listener_); listener_ = -1; }
        stop_noise();
        if (!socket_path_removed_) { unlink(kSocket); socket_path_removed_ = true; }
    }

private:
    json_object* request(json_object* req) {
        if (!json_object_is_type(req, json_type_object)) throw error("request must be a JSON object");
        json_object* action_obj = nullptr;
        if (!json_object_object_get_ex(req, "action", &action_obj) || !json_object_is_type(action_obj, json_type_string)) {
            throw error("missing or invalid field: action");
        }
        std::string action = json_object_get_string(action_obj);
        if (action == "ping") return response(true);
        if (action == "pink_noise") {
            const std::string path = canonical_file(json_string(req, "path"));
            if (path != canonical_file(kPinkNoise))
                throw error("pink-noise path is not the installed source");
            PcmFile pcm = read_wav(path); // Validate before disrupting current output.
            start_noise(std::move(pcm));
            { std::lock_guard<std::mutex> lock(state_mutex_); current_kind_ = "pink_noise"; }
            save_state(true);
            return response(true);
        }
        if (action == "pause") {
            json_object* paused = nullptr;
            if (!json_object_object_get_ex(req, "paused", &paused) || !json_object_is_type(paused, json_type_boolean)) throw error("paused must be boolean");
            set_paused(json_object_get_boolean(paused));
            return response(true);
        }
        if (action == "stop") {
            { std::lock_guard<std::mutex> lock(state_mutex_); current_kind_ = "off"; }
            stop_noise(); save_state(false);
            return response(true);
        }
        if (action == "volume_step") {
            json_object* delta_obj = nullptr;
            if (!json_object_object_get_ex(req, "delta", &delta_obj) || !json_object_is_type(delta_obj, json_type_int)) throw error("volume delta must be an integer");
            double delta = json_object_get_int(delta_obj);
            if (delta != -5 && delta != 5) throw error("volume delta must be -5 or 5");
            if (muted_) { muted_ = false; volume_ = muted_volume_; }
            volume_ = std::clamp(volume_ + delta, 0.0, 100.0);
            output_gain_.store(volume_ / 100.0);
            save_state(current_kind_ == "pink_noise");
            return response(true);
        }
        if (action == "mute_toggle") {
            if (!muted_) { muted_volume_ = volume_; muted_ = true; }
            else { muted_ = false; volume_ = muted_volume_; }
            output_gain_.store(muted_ ? 0.0 : volume_ / 100.0);
            save_state(current_kind_ == "pink_noise");
            return response(true);
        }
        if (action == "volume") {
            json_object* value = nullptr;
            if (!json_object_object_get_ex(req, "percent", &value) ||
                !(json_object_is_type(value, json_type_double) || json_object_is_type(value, json_type_int))) throw error("volume must be numeric");
            double volume = json_object_get_double(value);
            if (!std::isfinite(volume) || volume < 0 || volume > 100) throw error("volume must be between 0 and 100");
            volume_ = volume;
            if (muted_) muted_volume_ = volume;
            if (!muted_) output_gain_.store(volume_ / 100.0);
            save_state(current_kind_ == "pink_noise");
            return response(true);
        }
        if (action == "events") return events_response();
        throw error("unsupported audio action");
    }

    void handle(int fd) {
        std::string line;
        line.reserve(512);
        char ch;
        bool complete = false;
        while (line.size() <= kMaxRequest) {
            ssize_t n = recv(fd, &ch, 1, 0);
            if (n == 0) break;
            if (n < 0) { if (errno == EINTR) continue; break; }
            if (ch == '\n') { complete = true; break; }
            line.push_back(ch);
        }
        json_object* out = nullptr;
        if (!complete || line.size() > kMaxRequest) out = response(false, "invalid or oversized request");
        else {
            json_tokener* tokener = json_tokener_new();
            json_object* parsed = json_tokener_parse_ex(tokener, line.data(), static_cast<int>(line.size()));
            const auto parse_error = json_tokener_get_error(tokener);
            const std::size_t consumed = json_tokener_get_parse_end(tokener);
            bool trailing = false;
            for (std::size_t i = consumed; i < line.size(); ++i) trailing = trailing || (line[i] != ' ' && line[i] != '\t' && line[i] != '\r');
            json_tokener_free(tokener);
            if (!parsed || parse_error != json_tokener_success || trailing) {
                if (parsed) json_object_put(parsed);
                out = response(false, "invalid JSON request");
            }
            else {
                try { out = request(parsed); }
                catch (const std::exception& e) { out = response(false, e.what()); }
                json_object_put(parsed);
            }
        }
        const char* bytes = json_object_to_json_string_ext(out, JSON_C_TO_STRING_PLAIN);
        std::string reply = std::string(bytes) + "\n";
        std::size_t offset = 0;
        while (offset < reply.size()) {
            ssize_t n = send(fd, reply.data() + offset, reply.size() - offset, MSG_NOSIGNAL);
            if (n < 0) { if (errno == EINTR) continue; break; }
            offset += static_cast<std::size_t>(n);
        }
        json_object_put(out);
    }

    json_object* events_response() {
        json_object* result = response(true);
        json_object* list = json_object_new_array();
        std::lock_guard<std::mutex> lock(event_mutex_);
        while (!events_.empty()) {
            Event event = events_.front();
            events_.pop();
            json_object* item = json_object_new_object();
            json_object_object_add(item, "kind", json_object_new_string(event.kind.c_str()));
            json_object_object_add(item, "path", event.path.empty() ? json_object_new_null() : json_object_new_string(event.path.c_str()));
            json_object_object_add(item, "reason", event.reason.empty() ? json_object_new_null() : json_object_new_string(event.reason.c_str()));
            json_object_object_add(item, "message", event.message.empty() ? json_object_new_null() : json_object_new_string(event.message.c_str()));
            json_object_array_add(list, item);
        }
        json_object_object_add(result, "events", list);
        return result;
    }

    void start_noise() { start_noise(read_wav(kPinkNoise)); }
    void start_noise(PcmFile pcm) {
        stop_noise();
        {
            std::lock_guard<std::mutex> lock(noise_mutex_);
            noise_stop_ = false; noise_paused_ = false; noise_ready_ = false; noise_error_.clear();
            pcm_ = std::move(pcm);
        }
        noise_thread_ = std::thread(&AudioService::noise_loop, this);
        std::unique_lock<std::mutex> lock(noise_mutex_);
        if (!noise_cv_.wait_for(lock, std::chrono::seconds(8), [this]{ return noise_ready_ || !noise_error_.empty(); })) {
            lock.unlock(); stop_noise(); throw error("timed out opening audio output");
        }
        if (!noise_error_.empty()) {
            std::string why = noise_error_; lock.unlock(); stop_noise(); throw error("cannot start continuous WAV output: " + why);
        }
    }

    void noise_loop() {
        snd_pcm_t* pcm = nullptr;
        snd_pcm_hw_params_t* hw = nullptr;
        snd_pcm_sw_params_t* sw = nullptr;
        auto fail = [&](const std::string& message) {
            bool was_running;
            {
                std::lock_guard<std::mutex> lock(noise_mutex_);
                was_running = noise_ready_;
                noise_error_ = message; noise_cv_.notify_all();
            }
            std::cerr << "Continuous WAV output error: " << message << '\n';
            if (was_running) {
                std::lock_guard<std::mutex> lock(event_mutex_);
                events_.push(Event{"error", kPinkNoise, "error", message});
            }
        };
        int rc = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
        if (rc < 0) { fail(snd_strerror(rc)); return; }
        snd_pcm_hw_params_alloca(&hw);
        snd_pcm_hw_params_any(pcm, hw);
        unsigned channels = pcm_.channels, rate = pcm_.rate;
        if ((rc = snd_pcm_hw_params_set_access(pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
            (rc = snd_pcm_hw_params_set_format(pcm, hw, SND_PCM_FORMAT_S16_LE)) < 0 ||
            (rc = snd_pcm_hw_params_set_channels(pcm, hw, channels)) < 0 ||
            (rc = snd_pcm_hw_params_set_rate(pcm, hw, rate, 0)) < 0) {
            fail("audio device cannot accept the WAV's exact PCM format/rate: " + std::string(snd_strerror(rc)));
            snd_pcm_close(pcm); return;
        }
        unsigned buffer_us = 500000, period_us = 50000;
        int direction = 0;
        if ((rc = snd_pcm_hw_params_set_buffer_time_near(pcm, hw, &buffer_us, &direction)) < 0) {
            fail(snd_strerror(rc)); snd_pcm_close(pcm); return;
        }
        direction = 0;
        if ((rc = snd_pcm_hw_params_set_period_time_near(pcm, hw, &period_us, &direction)) < 0) {
            fail(snd_strerror(rc)); snd_pcm_close(pcm); return;
        }
        if ((rc = snd_pcm_hw_params(pcm, hw)) < 0) { fail(snd_strerror(rc)); snd_pcm_close(pcm); return; }
        snd_pcm_uframes_t buffer_frames = 0, period_frames = 0;
        unsigned actual_rate = 0;
        snd_pcm_hw_params_get_buffer_size(hw, &buffer_frames);
        snd_pcm_hw_params_get_period_size(hw, &period_frames, &direction);
        direction = 0;
        if ((rc = snd_pcm_hw_params_get_rate(hw, &actual_rate, &direction)) < 0 || actual_rate != rate) {
            fail("audio device changed the WAV sample rate"); snd_pcm_close(pcm); return;
        }
        snd_pcm_sw_params_alloca(&sw);
        snd_pcm_sw_params_current(pcm, sw);
        snd_pcm_sw_params_set_start_threshold(pcm, sw, buffer_frames);
        snd_pcm_sw_params_set_avail_min(pcm, sw, std::max<snd_pcm_uframes_t>(period_frames, 256));
        if ((rc = snd_pcm_sw_params(pcm, sw)) < 0 || (rc = snd_pcm_prepare(pcm)) < 0) {
            fail(snd_strerror(rc)); snd_pcm_close(pcm); return;
        }
        { std::lock_guard<std::mutex> lock(noise_mutex_); active_pcm_ = pcm; }

        std::size_t cursor = 0;
        const std::size_t frame_samples = channels;
        bool started = false;
        std::vector<int16_t> scaled(2048 * frame_samples);
        while (true) {
            std::unique_lock<std::mutex> lock(noise_mutex_);
            noise_cv_.wait(lock, [this]{ return noise_stop_ || !noise_paused_; });
            if (noise_stop_) break;
            lock.unlock();
            std::size_t available_samples = pcm_.samples.size() - cursor;
            snd_pcm_uframes_t frames = static_cast<snd_pcm_uframes_t>(std::min<std::size_t>(2048, available_samples / frame_samples));
            const double gain = output_gain_.load();
            const int16_t* output = pcm_.samples.data() + cursor;
            if (gain != 1.0) {
                for (std::size_t i = 0; i < frames * frame_samples; ++i) {
                    scaled[i] = static_cast<int16_t>(std::clamp(std::lrint(pcm_.samples[cursor + i] * gain), -32768L, 32767L));
                }
                output = scaled.data();
            }
            snd_pcm_sframes_t written = snd_pcm_writei(pcm, output, frames);
            if (written < 0) {
                // Do not prepare/restart after an underrun: that would create the gap this stream is designed to avoid.
                fail(std::string("continuous PCM stream stopped: ") + snd_strerror(static_cast<int>(written)));
                break;
            }
            if (written == 0) continue;
            cursor += static_cast<std::size_t>(written) * frame_samples;
            if (cursor >= pcm_.samples.size()) cursor = 0;
            if (!started && snd_pcm_state(pcm) == SND_PCM_STATE_RUNNING) {
                started = true;
                std::lock_guard<std::mutex> lock(noise_mutex_); noise_ready_ = true; noise_cv_.notify_all();
            }
        }
        { std::lock_guard<std::mutex> lock(noise_mutex_); active_pcm_ = nullptr; }
        snd_pcm_drop(pcm);
        snd_pcm_close(pcm);
        if (!started) { std::lock_guard<std::mutex> lock(noise_mutex_); noise_cv_.notify_all(); }
    }

    void stop_noise() {
        {
            std::lock_guard<std::mutex> lock(noise_mutex_);
            noise_stop_ = true; noise_cv_.notify_all();
        }
        if (noise_thread_.joinable()) noise_thread_.join();
    }

    void set_paused(bool paused) {
        std::string kind;
        { std::lock_guard<std::mutex> lock(state_mutex_); kind = current_kind_; }
        if (kind == "pink_noise") {
            std::lock_guard<std::mutex> lock(noise_mutex_);
            if (!active_pcm_) throw error("audio output is not ready");
            int rc = snd_pcm_pause(active_pcm_, paused ? 1 : 0);
            if (rc < 0) throw error(std::string("audio device cannot pause/resume stream: ") + snd_strerror(rc));
            noise_paused_ = paused; noise_cv_.notify_all();
        }
    }

    void load_saved_state() {
        std::ifstream file(kStateFile);
        std::string contents((std::istreambuf_iterator<char>(file)), {});
        json_object* root = json_tokener_parse(contents.c_str());
        if (!root || !json_object_is_type(root, json_type_object)) { if (root) json_object_put(root); return; }
        json_object* value = nullptr;
        if (json_object_object_get_ex(root, "state", &value) && json_object_is_type(value, json_type_string))
            saved_pink_ = std::string(json_object_get_string(value)) == "pink_noise";
        if (json_object_object_get_ex(root, "volume", &value) && (json_object_is_type(value, json_type_double) || json_object_is_type(value, json_type_int))) {
            double v = json_object_get_double(value);
            if (std::isfinite(v) && v >= 0 && v <= 100) volume_ = v;
        }
        if (json_object_object_get_ex(root, "muted", &value) && json_object_is_type(value, json_type_boolean)) muted_ = json_object_get_boolean(value);
        muted_volume_ = volume_;
        output_gain_.store(muted_ ? 0.0 : volume_ / 100.0);
        json_object_put(root);
    }
    void save_state(bool pink) {
        std::string temp = std::string(kStateFile) + ".tmp";
        int fd = open(temp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        if (fd < 0) throw error("cannot write audio state: " + std::string(std::strerror(errno)));
        saved_pink_ = pink;
        std::string value = std::string("{\"state\":\"") + (pink ? "pink_noise" : "off") +
            "\",\"volume\":" + std::to_string(volume_) + ",\"muted\":" + (muted_ ? "true" : "false") + "}\n";
        ssize_t n = write(fd, value.data(), value.size());
        bool good = n == static_cast<ssize_t>(value.size()) && fsync(fd) == 0;
        close(fd);
        if (!good || rename(temp.c_str(), kStateFile) != 0) { unlink(temp.c_str()); throw error("cannot commit audio state"); }
        int dirfd = open("/var/lib/bedtime-audio", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (dirfd >= 0) { fsync(dirfd); close(dirfd); }
    }

    int listener_ = -1;
    bool socket_path_removed_ = false;
    std::string current_kind_ = "off";
    std::mutex state_mutex_, event_mutex_, noise_mutex_;
    std::condition_variable noise_cv_;
    std::queue<Event> events_;
    std::thread noise_thread_;
    PcmFile pcm_;
    bool noise_stop_ = false, noise_paused_ = false, noise_ready_ = false;
    snd_pcm_t* active_pcm_ = nullptr;
    std::string noise_error_;
    bool saved_pink_ = false;
    std::atomic<double> output_gain_{1.0};
    double volume_ = 100.0, muted_volume_ = 100.0;
    bool muted_ = false;
};
} // namespace

int main() {
    std::signal(SIGTERM, on_signal);
    std::signal(SIGINT, on_signal);
    try {
        AudioService service;
        service.start();
        service.run();
        service.shutdown();
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "bedtime-audio-player: " << e.what() << '\n';
        return 1;
    }
}
