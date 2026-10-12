// SPDX-License-Identifier: MIT
/*
 * vm-pointer-bridge: guest-side helper for the Googlebook VM.
 *
 * QEMU's "usb-tablet" is an absolute pointer with buttons and a wheel, but Android does not accept it
 * (no BTN_TOUCH, no REL_X/REL_Y -> no input class). QEMU's "usb-wacom-tablet" is accepted but has no wheel,
 * and QEMU routes buttons/wheel to a single active device, so a second mouse cannot carry the wheel.
 *
 * This program reads the usb-tablet and re-emits it through /dev/uinput as two devices:
 *   "VM Pointer"  absolute pen-style pointer (ABS_X/ABS_Y, BTN_TOUCH, BTN_TOOL_PEN, BTN_LEFT/RIGHT/MIDDLE).
 *                 Its axis range equals the display size in pixels, so the mapping is 1:1 for any aspect
 *                 ratio. Uses vendor/product 056a:0000 so the existing Vendor_056a_Product_0000.idc applies.
 *   "VM Wheel"    relative mouse that only carries REL_WHEEL / REL_HWHEEL.
 */
#include <android/log.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

/* WARN level so it shows up in the serial logcat filter (*:W). */
#define LOG(...) __android_log_print(ANDROID_LOG_WARN, "vm-pointer-bridge", __VA_ARGS__)

static volatile sig_atomic_t g_stop;
static void on_signal(int s) { (void)s; g_stop = 1; }

static int find_tablet(void)
{
	char path[64], name[128];
	for (int i = 0; i < 64; i++) {
		snprintf(path, sizeof path, "/dev/input/event%d", i);
		int fd = open(path, O_RDONLY | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(name, 0, sizeof name);
		if (ioctl(fd, EVIOCGNAME(sizeof name - 1), name) >= 0 && strstr(name, "USB Tablet")) {
			LOG("tablet \"%s\" at %s", name, path);
			return fd;
		}
		close(fd);
	}
	return -1;
}

/* Preferred mode of the first connected DRM connector ("WxH" on the first line of its "modes" file). */
static int display_size(int *w, int *h)
{
	DIR *d = opendir("/sys/class/drm");
	if (!d)
		return -1;
	struct dirent *e;
	int ok = -1;
	while (ok < 0 && (e = readdir(d))) {
		if (strncmp(e->d_name, "card", 4) || !strchr(e->d_name, '-'))
			continue;
		char p[300], st[32] = "";
		snprintf(p, sizeof p, "/sys/class/drm/%s/status", e->d_name);
		FILE *f = fopen(p, "r");
		if (f) { if (!fgets(st, sizeof st, f)) st[0] = 0; fclose(f); }
		if (strncmp(st, "connected", 9))
			continue;
		snprintf(p, sizeof p, "/sys/class/drm/%s/modes", e->d_name);
		f = fopen(p, "r");
		if (!f)
			continue;
		int ww, hh;
		if (fscanf(f, "%dx%d", &ww, &hh) == 2 && ww > 0 && hh > 0) { *w = ww; *h = hh; ok = 0; }
		fclose(f);
	}
	closedir(d);
	return ok;
}

static int ui_open(void)
{
	int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0)
		fd = open("/dev/input/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
	return fd;
}

static void set_abs(int fd, int code, int min, int max)
{
	struct uinput_abs_setup a;
	memset(&a, 0, sizeof a);
	a.code = code;
	a.absinfo.minimum = min;
	a.absinfo.maximum = max;
	ioctl(fd, UI_ABS_SETUP, &a);
}

static int make_pointer(int w, int h)
{
	int fd = ui_open();
	if (fd < 0)
		return -1;
	ioctl(fd, UI_SET_EVBIT, EV_KEY);
	ioctl(fd, UI_SET_EVBIT, EV_ABS);
	ioctl(fd, UI_SET_EVBIT, EV_SYN);
	static const int keys[] = { BTN_TOUCH, BTN_TOOL_PEN, BTN_LEFT, BTN_RIGHT, BTN_MIDDLE };
	for (unsigned i = 0; i < sizeof keys / sizeof keys[0]; i++)
		ioctl(fd, UI_SET_KEYBIT, keys[i]);
	ioctl(fd, UI_SET_ABSBIT, ABS_X);
	ioctl(fd, UI_SET_ABSBIT, ABS_Y);
	ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_POINTER);
	set_abs(fd, ABS_X, 0, w - 1);
	set_abs(fd, ABS_Y, 0, h - 1);
	struct uinput_setup s;
	memset(&s, 0, sizeof s);
	snprintf(s.name, sizeof s.name, "VM Pointer");
	s.id.bustype = BUS_USB;
	s.id.vendor = 0x056a;	/* matches /vendor/usr/idc/Vendor_056a_Product_0000.idc */
	s.id.product = 0x0000;
	s.id.version = 1;
	if (ioctl(fd, UI_DEV_SETUP, &s) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

static int make_wheel(void)
{
	int fd = ui_open();
	if (fd < 0)
		return -1;
	ioctl(fd, UI_SET_EVBIT, EV_KEY);
	ioctl(fd, UI_SET_EVBIT, EV_REL);
	ioctl(fd, UI_SET_EVBIT, EV_SYN);
	ioctl(fd, UI_SET_KEYBIT, BTN_LEFT);	/* BTN_MOUSE + REL_X/REL_Y => Android "cursor" class */
	ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT);
	ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);
	ioctl(fd, UI_SET_RELBIT, REL_X);
	ioctl(fd, UI_SET_RELBIT, REL_Y);
	ioctl(fd, UI_SET_RELBIT, REL_WHEEL);
	ioctl(fd, UI_SET_RELBIT, REL_HWHEEL);
	struct uinput_setup s;
	memset(&s, 0, sizeof s);
	snprintf(s.name, sizeof s.name, "VM Wheel");
	s.id.bustype = BUS_USB;
	s.id.vendor = 0x1d6b;
	s.id.product = 0x0104;
	s.id.version = 1;
	if (ioctl(fd, UI_DEV_SETUP, &s) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

static void emit(int fd, int type, int code, int value)
{
	struct input_event ev;
	memset(&ev, 0, sizeof ev);
	ev.type = type;
	ev.code = code;
	ev.value = value;
	if (write(fd, &ev, sizeof ev) < 0 && errno != EAGAIN)
		LOG("write failed: %s", strerror(errno));
}

static int run_once(void)
{
	int tab = find_tablet();
	if (tab < 0)
		return 1;
	struct input_absinfo ax, ay;
	if (ioctl(tab, EVIOCGABS(ABS_X), &ax) < 0 || ioctl(tab, EVIOCGABS(ABS_Y), &ay) < 0 ||
	    ax.maximum <= ax.minimum || ay.maximum <= ay.minimum) {
		LOG("tablet has no usable ABS_X/ABS_Y");
		close(tab);
		return 1;
	}
	int w = 1440, h = 1080;
	if (display_size(&w, &h) < 0)
		LOG("display size unknown, assuming %dx%d", w, h);
	LOG("tablet range %d..%d x %d..%d -> display %dx%d", ax.minimum, ax.maximum, ay.minimum, ay.maximum, w, h);
	ioctl(tab, EVIOCGRAB, 1);

	int ptr = make_pointer(w, h), whl = make_wheel();
	if (ptr < 0 || whl < 0) {
		LOG("cannot create uinput devices: %s", strerror(errno));
		close(tab);
		if (ptr >= 0) close(ptr);
		if (whl >= 0) close(whl);
		return 1;
	}
	LOG("running");
	/* "VM Wheel" is a real mouse for Android and has its own cursor; scroll events are delivered where that
	 * cursor is. Park it at the top-left corner (movement is clamped to the display), then keep it under the
	 * tablet position: before each wheel report we send the (cx,cy)->(x,y) delta as a single relative move. */
	int cx = 0, cy = 0;
	emit(whl, EV_REL, REL_X, -20000); emit(whl, EV_REL, REL_Y, -20000); emit(whl, EV_SYN, SYN_REPORT, 0);

	int x = 0, y = 0, left = 0, right = 0, mid = 0, wheel = 0, hwheel = 0, prox = 0;
	int dirty_ptr = 0, dirty_btn = 0;
	int dbg_wheel = 0, dbg_btn = 0, dbg_abs = 0;
	int rc = 0;
	struct pollfd pfd = { .fd = tab, .events = POLLIN };
	while (!g_stop) {
		int pr = poll(&pfd, 1, 1000);
		if (pr < 0 && errno == EINTR)
			continue;
		if (pr < 0 || (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))) { rc = 1; break; }
		if (!(pfd.revents & POLLIN))
			continue;
		struct input_event ev[64];
		ssize_t n = read(tab, ev, sizeof ev);
		if (n < 0) {
			if (errno == EAGAIN || errno == EINTR) continue;
			rc = 1;
			break;
		}
		for (size_t i = 0; i < (size_t)n / sizeof ev[0]; i++) {
			switch (ev[i].type) {
			case EV_ABS:
				if (dbg_abs < 3) { LOG("abs code=%d value=%d", ev[i].code, ev[i].value); dbg_abs++; }
				if (ev[i].code == ABS_X) {
					long v = ev[i].value - ax.minimum;
					x = (int)(v * (w - 1) / (ax.maximum - ax.minimum));
					dirty_ptr = 1;
				} else if (ev[i].code == ABS_Y) {
					long v = ev[i].value - ay.minimum;
					y = (int)(v * (h - 1) / (ay.maximum - ay.minimum));
					dirty_ptr = 1;
				}
				break;
			case EV_KEY:
				if (dbg_btn < 6) { LOG("key code=%d value=%d", ev[i].code, ev[i].value); dbg_btn++; }
				if (ev[i].code == BTN_LEFT) left = ev[i].value ? 1 : 0, dirty_btn = 1;
				else if (ev[i].code == BTN_RIGHT) right = ev[i].value ? 1 : 0, dirty_btn = 1;
				else if (ev[i].code == BTN_MIDDLE) mid = ev[i].value ? 1 : 0, dirty_btn = 1;
				break;
			case EV_REL:
				if (dbg_wheel < 3) { LOG("rel code=%d value=%d", ev[i].code, ev[i].value); dbg_wheel++; }
				if (ev[i].code == REL_WHEEL) wheel += ev[i].value;
				else if (ev[i].code == REL_HWHEEL) hwheel += ev[i].value;
				break;
			case EV_SYN:
				if (ev[i].code != SYN_REPORT)
					break;
				if (dirty_ptr || dirty_btn) {
					if (!prox) { emit(ptr, EV_KEY, BTN_TOOL_PEN, 1); prox = 1; }
					emit(ptr, EV_ABS, ABS_X, x);
					emit(ptr, EV_ABS, ABS_Y, y);
					emit(ptr, EV_KEY, BTN_TOUCH, left || right || mid);
					emit(ptr, EV_KEY, BTN_LEFT, left);
					emit(ptr, EV_KEY, BTN_RIGHT, right);
					emit(ptr, EV_KEY, BTN_MIDDLE, mid);
					emit(ptr, EV_SYN, SYN_REPORT, 0);
					dirty_ptr = dirty_btn = 0;
				}
				if (wheel || hwheel) {
					if (x != cx || y != cy) {
						emit(whl, EV_REL, REL_X, x - cx);
						emit(whl, EV_REL, REL_Y, y - cy);
						cx = x; cy = y;
					}
					if (wheel) emit(whl, EV_REL, REL_WHEEL, wheel);
					if (hwheel) emit(whl, EV_REL, REL_HWHEEL, hwheel);
					emit(whl, EV_SYN, SYN_REPORT, 0);
					if (dbg_wheel <= 3) LOG("forwarded wheel=%d hwheel=%d at (%d,%d)", wheel, hwheel, cx, cy);
					wheel = hwheel = 0;
				}
				break;
			}
		}
	}
	ioctl(ptr, UI_DEV_DESTROY);
	ioctl(whl, UI_DEV_DESTROY);
	close(ptr);
	close(whl);
	close(tab);
	return rc;
}

int main(void)
{
	signal(SIGTERM, on_signal);
	signal(SIGINT, on_signal);
	while (!g_stop) {
		if (run_once() == 0)
			break;
		sleep(1);	/* tablet not there yet, or it went away: retry */
	}
	return 0;
}
