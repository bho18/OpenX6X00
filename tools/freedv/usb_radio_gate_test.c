// SPDX-License-Identifier: BSD-2-Clause
/* Include the production USB adapter. Only the radio/parameter/physical I/O
 * endpoints are substituted; the admission, cancellation and restoration
 * checks below are the actual application code. No radio is accessed. */
#define _GNU_SOURCE
#define ENABLE_FREEDV_DIAGNOSTICS 1
#undef NDEBUG
#include "src/hw/hw.h"
#include "src/freedv/usb_radio.h"
#include "src/freedv/usb_ptt.h"
#include "src/freedv/packet_tx.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

#define BOARD_DEV_KEYPAD "/dev/null"
enum {RADIO_RX, RADIO_TX};
enum {cfg_band_split=1,cfg_band_tx_shift,cfg_rit,cfg_xit,cfg_fg_freq,cfg_band_current_vfo};
typedef struct {bool known,fresh,tx;} fd_observation;
static int state=RADIO_RX;
static void *freedv_owner;
static atomic_uint freedv_base_errors;
static atomic_bool modem_on;
static bool morse_on;
static struct {hw_freedv_cached_snapshot_t saved;} voice_radio;
static pthread_mutex_t radio_mutex=PTHREAD_MUTEX_INITIALIZER;
static freedv_packet_tx_tracker packet_tx_tracker=FREEDV_PACKET_TX_TRACKER_INIT;
static uint64_t clock_ms=10000;
static hw_freedv_cached_snapshot_t cached;
static bool panel_pressed,panel_valid=true,writer_allowed=true,legal_frequency=true;
static unsigned swr_count,modem_on_writes,ptt_on_writes,pin_on_writes,all_writes;
static hw_param_t stop_during_write=(hw_param_t)-1;
static bool stop_during_modem_on;
static bool test_panel_poll(int fd,atomic_uint_fast64_t *releases,bool *pressed){(void)fd;(void)releases;*pressed=panel_pressed;return panel_valid;}
#define fd_usb_panel_poll test_panel_poll
static void radio_lock(void){assert(!pthread_mutex_lock(&radio_mutex));}
static void radio_unlock(void){assert(!pthread_mutex_unlock(&radio_mutex));}
static uint64_t get_time(void){return clock_ms;}
static unsigned radio_swr_trips(void){return swr_count;}
static bool radio_freedv_startup_waiting(void){return false;}
static bool freedv_busy(void){return false;}
bool hw_tx_keyed_by_gui(void){return cached.route.modem || cached.route.ptt;}
static bool ctl_freedv_acquire(void){return true;}
static bool ctl_freedv_release(bool restored){return restored;}
static bool fd_writer_voice_begin(void){return writer_allowed;}
static void fd_writer_voice_end(void){}
static int32_t param_i_get(int key){(void)key;return 0;}
static int32_t cparam_i_get(int key){assert(key==cfg_fg_freq);return 7074000;}
static int cfg_transverter_shift_for(int32_t hz){(void)hz;return 0;}
static bool cfg_is_tx_freq(int32_t hz){return legal_frequency && hz==7074000;}
static int32_t radio_tx_freq(void){return 7074000;}
static bool freedv_observe_locked(fd_observation *out){
    freedv_packet_tx_observation p=freedv_packet_tx_read(&packet_tx_tracker,clock_ms);
    *out=(fd_observation){p.known,p.known && p.age_ms<=250,p.tx};return out->known;
}
static bool freedv_rx(const fd_observation *o){return o->known && o->fresh && !o->tx;}
static void voice_restore_on_gui(void *arg){cached=voice_radio.saved;*(bool *)arg=true;}
bool hw_get_freedv_cached_snapshot(hw_freedv_cached_snapshot_t *out){*out=cached;return true;}
bool hw_resend_all(void){return true;}
bool hw_set_i(hw_param_t p,int32_t value){
    ++all_writes;
    if(p==HW_MODEM){cached.route.modem=value!=0;if(value)++modem_on_writes;}
    if(p==HW_PTT){cached.route.ptt=value!=0;if(value)++ptt_on_writes;}
    if(p==HW_RECORD)cached.route.record=value!=0;
    if(p==HW_LINE_IN)cached.line_in=(uint8_t)value;
    if(p==HW_MONI)cached.monitor_level=(uint8_t)value;
    if(p==HW_VOX)cached.vox_enabled=value!=0;
    if(p==HW_FILTER1_LOW)cached.rx_filter_low_hz=(int16_t)value;
    if(p==HW_FILTER1_HIGH)cached.rx_filter_high_hz=(int16_t)value;
    if((p==stop_during_write && !value) || (p==HW_MODEM && value && stop_during_modem_on))radio_usb_inhibit(NULL);
    return true;
}
bool hw_set_f(hw_param_t p,float value){assert(p==HW_TXPWR_F);cached.requested_power_tenths_watt=(uint8_t)(value*10);return true;}
bool hw_vfo_set_mode(radio_vfo_t vfo,radio_mode_t mode){if(vfo==RADIO_VFO_A)cached.backend_mode_a=mode;else cached.backend_mode_b=mode;return true;}
void hw_vfo_set_agc(radio_vfo_t vfo,radio_agc_t agc){(void)vfo;(void)agc;}
void hw_vfo_set_pre(radio_vfo_t vfo,bool on){(void)vfo;(void)on;}
void hw_vfo_set_att(radio_vfo_t vfo,bool on){(void)vfo;(void)on;}
void hw_pin_set(hw_pin_t pin,bool on){assert(pin==HW_PIN_MORSE_KEY);if(on)++pin_on_writes;}
#include "src/freedv/usb_radio.inc"
#undef fd_usb_panel_poll

static void observed(bool tx){++clock_ms;freedv_packet_tx_note(&packet_tx_tracker,tx,clock_ms);state=tx?RADIO_TX:RADIO_RX;}
static void claim(void){
    panel_pressed=false;panel_valid=true;writer_allowed=true;legal_frequency=true;
    cached=(hw_freedv_cached_snapshot_t){.vfo_a_hz=7074000,.vfo_b_hz=7074000,.requested_power_tenths_watt=50};
    stop_during_write=(hw_param_t)-1;stop_during_modem_on=false;
    observed(false);assert(radio_usb_claim());assert(radio_usb_owned());assert(!atomic_load(&usb_radio.rf_allowed));
}
static void release(void){
    radio_usb_clear(NULL,true);panel_pressed=false;observed(false);
    assert(!radio_usb_restore());observed(false);assert(radio_usb_restore());assert(radio_usb_release());assert(!radio_usb_owned());
}
int main(void){
    claim();unsigned initial=modem_on_writes;
    panel_pressed=true;assert(!radio_usb_key(NULL,0));assert(modem_on_writes==initial);
    panel_pressed=false;clock_ms+=251;assert(!radio_usb_allow_tx(true));
    observed(true);assert(!radio_usb_allow_tx(true));observed(false);
    panel_pressed=true;assert(!radio_usb_allow_tx(true));panel_pressed=false;
    panel_valid=false;assert(!radio_usb_allow_tx(true));panel_valid=true;
    ++swr_count;assert(!radio_usb_allow_tx(true));--swr_count;
    assert(radio_usb_allow_tx(true));panel_pressed=true;
    assert(!radio_usb_key(NULL,1));assert(modem_on_writes==initial); /* retained-release mismatch */
    writer_allowed=false;assert(!radio_usb_key(NULL,0));writer_allowed=true;
    legal_frequency=false;assert(!radio_usb_key(NULL,0));legal_frequency=true;
    assert(radio_usb_key(NULL,0));assert(cached.route.modem && modem_on_writes==initial+1);
    radio_usb_clear(NULL,false);assert(!cached.route.modem && !cached.route.ptt);
    radio_usb_allow_tx(false);assert(!radio_usb_key(NULL,0));assert(modem_on_writes==initial+1);
    release();

    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;
    stop_during_write=HW_PTT;assert(!radio_usb_key(NULL,0));
    assert(atomic_load(&usb_radio.halted));assert(!atomic_load(&usb_radio.rf_allowed));assert(!atomic_load(&modem_on));
    assert(modem_on_writes==initial && !cached.route.modem);release();

    claim();initial=modem_on_writes;assert(radio_usb_allow_tx(true));panel_pressed=true;
    stop_during_modem_on=true;assert(!radio_usb_key(NULL,0));
    assert(modem_on_writes==initial+1); /* already in flight when Stop arrives */
    assert(atomic_load(&usb_radio.halted) && !atomic_load(&modem_on));
    assert(!cached.route.modem && !cached.route.ptt); /* compensating OFF before return */
    assert(!radio_usb_key(NULL,0));assert(modem_on_writes==initial+1);release();
    assert(!ptt_on_writes && !pin_on_writes && all_writes);
    puts("PASS production USB radio gate: RF-disabled default, fresh/released local enable, cancellation before/in-flight MODEM-on and compensating OFF; stub hardware only");
    return 0;
}
