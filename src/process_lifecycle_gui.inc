/* Included by gui_main.c and its focused production-wiring fixture. */
static bool lifecycle_active(void *unused) {
    (void)unused;
#if defined(ENABLE_FREEDV_USB_VOICE) || defined(ENABLE_FREEDV_USB_TEST)
    return freedv_usb_active();
#else
    return false;
#endif
}
static void lifecycle_cleanup(void *unused) {
    (void)unused;
    freedv_navigation_signal_terminate();
}
static void lifecycle_abort(void *unused) {
    (void)unused;
    freedv_usb_deadline_abort();
}
static void lifecycle_bypass(void *unused) {
    (void)unused;
    wr12_shutdown_bypass();
}
static bool lifecycle_initialize(void) {
    const process_lifecycle_ops lifecycle = {.active=lifecycle_active,
        .request_cleanup=lifecycle_cleanup,.abort_requested=lifecycle_abort,
        .bypass=lifecycle_bypass,.journal_path=PROCESS_LIFECYCLE_JOURNAL_PATH};
    return process_lifecycle_init(&lifecycle);
}
