# Xiegu X6100 LVGL GUI - top level build
#
# Copyright (c) 2026 Giovanni Ricciardi IU1WXY
#
# Two ways in: the radio build (buildroot toolchain, Cortex-A7 with NEON) and
# the PC test build (cmake -DENABLE_TESTING=YES, see run_tests.sh).

cmake_minimum_required(VERSION 3.23)

# The GUI on this PC: an SDL window for the panel, the stub radio behind hw.h, the data in a
# directory. host/build.sh configures, builds and runs it; see host/README.md
option(HOST_BUILD "Build the GUI for this PC (SDL window, stub radio)" OFF)
option(ENABLE_FREEDV_DIAGNOSTICS "Build experimental FreeDV diagnostic state support" OFF)
set(X6200_CONTROL_INCLUDE_DIR "" CACHE PATH "X6200Control include directory for optional host diagnostic tests")
set(HOST_DATA_DIR "${CMAKE_BINARY_DIR}/data" CACHE PATH "The host build's data directory (the radio's /mnt)")

# All C code is C11
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

project(x6100_gui)

if(ENABLE_FREEDV_DIAGNOSTICS)
        # This state prototype does not access the radio and is not a diagnostic UI.
        add_library(freedv_diagnostic_state STATIC tools/freedv/diagnostic_state.c)
        target_include_directories(freedv_diagnostic_state PUBLIC tools/freedv)
        if(HOST_BUILD)
                enable_testing()
                find_package(Threads REQUIRED)
                add_executable(freedv_session_controller_test tools/freedv/session_controller.c
                               tools/freedv/session_controller_test.c src/freedv/watchdog.c)
                target_include_directories(freedv_session_controller_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_session_controller_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_session_controller_test PRIVATE freedv_diagnostic_state Threads::Threads)
                add_test(NAME freedv_session_controller COMMAND freedv_session_controller_test)
                add_executable(freedv_wav_capture_test src/freedv/wav_capture.c tools/freedv/wav_capture_test.c)
                target_include_directories(freedv_wav_capture_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_definitions(freedv_wav_capture_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_wav_capture_test PRIVATE -UNDEBUG)
                target_link_options(freedv_wav_capture_test PRIVATE -Wl,--wrap=fwrite)
                target_link_libraries(freedv_wav_capture_test PRIVATE Threads::Threads)
                add_test(NAME freedv_wav_capture COMMAND freedv_wav_capture_test)
                add_executable(freedv_capture_ring_test src/freedv/capture_ring.c tools/freedv/capture_ring_test.c)
                target_include_directories(freedv_capture_ring_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_capture_ring_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_capture_ring_test PRIVATE Threads::Threads)
                add_test(NAME freedv_capture_ring COMMAND freedv_capture_ring_test)
                # Exercise the real capture callback with mocked PulseAudio and DSP.
                add_executable(freedv_audio_capture_test tools/freedv/audio_capture_test.c src/freedv/capture_ring.c src/freedv/wav_capture.c)
                target_include_directories(freedv_audio_capture_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_audio_capture_test PRIVATE BOARD_X6200=1 ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_audio_capture_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_audio_capture_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_audio_capture_test PRIVATE Threads::Threads)
                add_test(NAME freedv_audio_capture COMMAND freedv_audio_capture_test)
                add_executable(freedv_audio_playback_test tools/freedv/audio_playback_test.c)
                target_include_directories(freedv_audio_playback_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_audio_playback_test PRIVATE BOARD_X6200=1 ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_audio_playback_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_audio_playback_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_audio_playback_test PRIVATE Threads::Threads)
                add_test(NAME freedv_audio_playback COMMAND freedv_audio_playback_test)
                add_executable(freedv_packet_tx_test src/freedv/packet_tx.c tools/freedv/packet_tx_test.c)
                target_include_directories(freedv_packet_tx_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_packet_tx_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_packet_tx_test PRIVATE Threads::Threads)
                add_test(NAME freedv_packet_tx COMMAND freedv_packet_tx_test)
                add_executable(freedv_stub_snapshot_test src/hw/stub/hw_stub.c tools/freedv/stub_snapshot_test.c)
                target_include_directories(freedv_stub_snapshot_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_definitions(freedv_stub_snapshot_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_stub_snapshot_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_stub_snapshot_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_stub_snapshot_test PRIVATE m)
                add_test(NAME freedv_stub_snapshot COMMAND freedv_stub_snapshot_test)
                add_executable(freedv_watchdog_test src/freedv/watchdog.c tools/freedv/watchdog_test.c)
                target_include_directories(freedv_watchdog_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_watchdog_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_watchdog_test PRIVATE Threads::Threads)
                add_test(NAME freedv_watchdog COMMAND freedv_watchdog_test)
                add_executable(freedv_control_lease_test src/control_owner.c tools/freedv/control_lease_test.c)
                target_include_directories(freedv_control_lease_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src)
                target_compile_definitions(freedv_control_lease_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_control_lease_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_control_lease_test PRIVATE Threads::Threads)
                add_test(NAME freedv_control_lease COMMAND freedv_control_lease_test)
                add_executable(freedv_diagnostic_state_test tools/freedv/diagnostic_state_test.c)
                target_compile_options(freedv_diagnostic_state_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_diagnostic_state_test PRIVATE freedv_diagnostic_state)
                add_test(NAME freedv_diagnostic_state COMMAND freedv_diagnostic_state_test)
                if(X6200_CONTROL_INCLUDE_DIR)
                        add_executable(freedv_x6200_route_test src/hw/x6200/hw_x6200.c
                                       tools/freedv/x6200_route_snapshot_test.c)
                        target_include_directories(freedv_x6200_route_test PRIVATE ${X6200_CONTROL_INCLUDE_DIR})
                        target_compile_definitions(freedv_x6200_route_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                        target_compile_options(freedv_x6200_route_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                        target_link_options(freedv_x6200_route_test PRIVATE -Wl,--gc-sections -Wl,--wrap=dlsym)
                        add_test(NAME freedv_x6200_route_snapshot COMMAND freedv_x6200_route_test)
                endif()
        endif()
endif()

# Headers are included from the top: lv_conf.h, lvgl/, src/...
include_directories(.)

if(ENABLE_TESTING)
        # Tests run on the PC: Catch2 and the radio control library are
        # fetched. Everything from here on, the control library included,
        # is built with LVGL drawing without NEON.
        include(FetchContent)
        FetchContent_Declare(Catch2
                GIT_REPOSITORY https://github.com/catchorg/Catch2.git
                GIT_TAG        v3.4.0)
        FetchContent_MakeAvailable(Catch2)
        add_compile_definitions(LV_DRAW_NEON=0)
        FetchContent_Declare(aether_x6100_control
                GIT_REPOSITORY https://github.com/gdyuldin/X6100Control.git
                GIT_TAG        v0.16.1)
        FetchContent_MakeAvailable(aether_x6100_control)
elseif(HOST_BUILD)
        # This PC: no NEON, the SDL window driver, the host board (board.h), the data and share
        # directories under the build directory; liquid-dsp and ft8_lib from build-host/deps
        # (host/build.sh puts them there)
        add_compile_definitions(LV_DRAW_NEON=0 HOST_BUILD=1 USE_SDL=1 USE_DRM=0 USE_FBDEV=0)
        add_compile_definitions(BOARD_DATA="${HOST_DATA_DIR}" BOARD_SHARE="${CMAKE_BINARY_DIR}/share")
        set(LV_DRIVERS_SDL ON)
        set(HW_BACKEND "stub" CACHE STRING "The radio backend: x6100 or stub")
        set(HOST_DEPS ${CMAKE_SOURCE_DIR}/build-host/deps)
        include_directories(${HOST_DEPS}/include)
        link_directories(${HOST_DEPS}/lib)
        add_link_options(-Wl,-rpath,${HOST_DEPS}/lib)
        # What the image ships read-only, staged as the host's share directory
        file(COPY rootfs/usr/share/x6100/ DESTINATION ${CMAKE_BINARY_DIR}/share)
        file(COPY images DESTINATION ${CMAKE_BINARY_DIR}/share)
        file(COPY third-party/cty/cty.dat DESTINATION ${CMAKE_BINARY_DIR}/share)
else()
        # Allwinner A33: Cortex-A7, hard float, NEON
        add_compile_options(-mtune=cortex-a7 -mcpu=cortex-a7 -mfloat-abi=hard -mfpu=neon-vfpv4)
        # Unused functions and data are not linked
        add_compile_options(-ffunction-sections -fdata-sections)
        add_link_options(-Wl,--gc-sections)
endif()

# The radio behind src/hw/hw.h: the X6100 or the X6200 through its control library, or the
# stub (a radio made of nothing, for the GUI on a desktop and as the template for a new radio's
# backend). The host build above chose the stub already
set(HW_BACKEND "x6100" CACHE STRING "The radio backend: x6100, x6200 or stub")
if(HW_BACKEND STREQUAL "x6200")
        add_compile_definitions(BOARD_X6200=1)
endif()

add_subdirectory(lvgl)

if(ENABLE_TESTING AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests)
        # tests/ lives in the private dev repository
        enable_testing()
        add_subdirectory(tests)
else()
        add_subdirectory(third-party/js8core)     # JS8Call's modem, for src/js8
        add_subdirectory(third-party/wsprd)
        add_subdirectory(src)
        add_subdirectory(lv_drivers)
        add_subdirectory(sql)
        if(NOT HOST_BUILD)
                add_subdirectory(btcat)       # the radio's own programs
                add_subdirectory(bootanim)
                add_subdirectory(third-party/ardopcf)     # the Winlink modem, for Pat
        endif()

        # Installed into the buildroot target: the GUI, the rootfs overlay
        # (init scripts, tools, data) and the images
        install(TARGETS ${PROJECT_NAME} DESTINATION sbin)
        install(DIRECTORY rootfs/ DESTINATION / USE_SOURCE_PERMISSIONS)
        install(DIRECTORY images DESTINATION share/x6100)
        # Callsign -> country for the FT8/FT4 table (AD1C, country-files.com)
        install(FILES third-party/cty/cty.dat DESTINATION share/x6100)
        # No folder theme is installed: themes/RS is cut from a screenshot of Icom's RS-BA1
        # (themes/make_rs.py, personal use only)
        # Pat, the Winlink client: built outside, by tools/pat/build.sh (buildroot/local-build.sh
        # passes its path), and installed as /usr/bin/pat, so the update bundle carries it too
        set(PAT_BINARY "" CACHE FILEPATH "Pat for the radio (tools/pat/build.sh)")
        if(PAT_BINARY)
                install(PROGRAMS ${PAT_BINARY} DESTINATION bin RENAME pat)
        endif()
        # The apps' version, for the web configurator and the scripts. Read at configure time, so a
        # changed main.h must configure again: a plain rebuild left the old number in the file (and
        # in the update bundle, whose radios then thought they ran 0.1.0)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS src/main.h)
        file(STRINGS src/main.h APP_VERSION REGEX "define VERSION ")
        string(REGEX REPLACE ".* v([0-9.]+).*" "\\1" APP_VERSION "${APP_VERSION}")
        file(WRITE ${CMAKE_BINARY_DIR}/app-version "${APP_VERSION}\n")
        install(FILES ${CMAKE_BINARY_DIR}/app-version DESTINATION share/x6100)
endif()
