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
option(ENABLE_FREEDV_NATIVE_CODEC "Link pinned RADE streaming engines" OFF)
option(ENABLE_FREEDV_VOICE "Build qualification-gated native FreeDV mode" OFF)
option(ENABLE_FREEDV_NATIVE_VALIDATION "Build locally authorized, capability-gated native workflow validation" OFF)
option(ENABLE_FREEDV_USB_TEST "Build separate stock-baseband USB headset RF development mode" OFF)
option(ENABLE_FREEDV_USB_VOICE "Build operational USB-headset FreeDV on the normal radio screen" OFF)
if(ENABLE_FREEDV_USB_TEST)
    # Existing diagnostic/qualification build recipes still include the USB UI.
    set(ENABLE_FREEDV_USB_VOICE ON)
endif()
if(ENABLE_FREEDV_USB_VOICE AND NOT ENABLE_FREEDV_VOICE)
    message(FATAL_ERROR "USB headset mode requires native codec/voice support")
endif()
if(ENABLE_FREEDV_NATIVE_VALIDATION AND (NOT ENABLE_FREEDV_DIAGNOSTICS OR NOT ENABLE_FREEDV_VOICE))
    message(FATAL_ERROR "Native validation requires diagnostics and native voice; it never registers a production profile")
endif()
option(FREEDV_RETEST_20261102 "Reuse reviewed unkeyed/Stop observations for bounded X6200 regression cases" OFF)
if(FREEDV_RETEST_20261102 AND NOT ENABLE_FREEDV_DIAGNOSTICS)
    message(FATAL_ERROR "Retest access is restricted to diagnostic images")
endif()
option(FREEDV_HOST_TEST_PROFILE "Virtual-host-only native mode integration fixture" OFF)
set(FREEDV_BUILD_ID "unqualified-development" CACHE STRING "Source/build manifest identity for qualified native profiles")
set(FREEDV_SOURCE_COMMIT "unqualified-development" CACHE STRING "Exact committed source identity carried by USB session evidence")
set(FREEDV_CONTROL_ID "unqualified-development" CACHE STRING "Pinned control-library source/package identity")
if(FREEDV_HOST_TEST_PROFILE AND (NOT HOST_BUILD OR NOT ENABLE_FREEDV_VOICE))
    message(FATAL_ERROR "The simulated FreeDV profile requires HOST_BUILD and ENABLE_FREEDV_VOICE")
endif()

set(FREEDV_RADE_SOURCE "" CACHE PATH "Pinned patched rade_c/src directory")
set(FREEDV_OPUS_BUILD "" CACHE PATH "Pinned RADE Opus build directory")
set(FREEDV_RADE_LIBRARY "" CACHE FILEPATH "Matching target librade")
set(FREEDV_OPUS_LIBRARY "" CACHE FILEPATH "Matching target static libopus")
set(FREEDV_SAMPLERATE_LIBRARY "" CACHE FILEPATH "Matching target static libsamplerate")
set(FREEDV_SAMPLERATE_INCLUDE_DIR "" CACHE PATH "Matching samplerate header directory")
set(FREEDV_NE10_LIBRARY "" CACHE FILEPATH "Optional matching target NE10 library")

set(X6200_CONTROL_INCLUDE_DIR "" CACHE PATH "X6200Control include directory for optional host diagnostic tests")
set(HOST_DATA_DIR "${CMAKE_BINARY_DIR}/data" CACHE PATH "The host build's data directory (the radio's /mnt)")

# All C code is C11
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

project(x6100_gui)

if(ENABLE_FREEDV_VOICE AND NOT ENABLE_FREEDV_NATIVE_CODEC)
        message(FATAL_ERROR "Native voice requires pinned ENABLE_FREEDV_NATIVE_CODEC")
endif()

if(ENABLE_FREEDV_NATIVE_CODEC)
        if(NOT ENABLE_FREEDV_DIAGNOSTICS AND NOT ENABLE_FREEDV_VOICE)
                message(FATAL_ERROR "Select native voice and/or diagnostics with the native codec")
        endif()
        foreach(required FREEDV_RADE_SOURCE FREEDV_OPUS_BUILD FREEDV_RADE_LIBRARY FREEDV_OPUS_LIBRARY FREEDV_SAMPLERATE_LIBRARY)
                if(NOT EXISTS "${${required}}")
                        message(FATAL_ERROR "Provide pinned matching-target ${required}")
                endif()
        endforeach()
        if(NOT FREEDV_SAMPLERATE_INCLUDE_DIR)
                find_path(FREEDV_SAMPLERATE_INCLUDE_DIR samplerate.h REQUIRED)
        endif()
        if(NOT EXISTS "${FREEDV_OPUS_BUILD}/config.h")
                message(FATAL_ERROR "Pinned Opus config.h is required")
        endif()
        find_package(Threads REQUIRED)
        add_library(freedv_native_codec STATIC tools/freedv/rade_stream.c src/freedv/capture_ring.c
                    src/freedv/voice_engine.c)
        target_compile_definitions(freedv_native_codec PRIVATE RADE_ACQ_PARALLEL=1)
        target_include_directories(freedv_native_codec PRIVATE ${CMAKE_SOURCE_DIR} ${FREEDV_RADE_SOURCE}
                ${FREEDV_OPUS_BUILD} ${FREEDV_OPUS_BUILD}/include ${FREEDV_OPUS_BUILD}/dnn ${FREEDV_OPUS_BUILD}/celt
                ${FREEDV_SAMPLERATE_INCLUDE_DIR})
        target_compile_options(freedv_native_codec PRIVATE -include ${FREEDV_OPUS_BUILD}/config.h)
        target_link_libraries(freedv_native_codec PUBLIC ${FREEDV_RADE_LIBRARY} ${FREEDV_OPUS_LIBRARY}
                ${FREEDV_SAMPLERATE_LIBRARY} Threads::Threads m)
        if(FREEDV_NE10_LIBRARY)
                target_link_libraries(freedv_native_codec PUBLIC ${FREEDV_NE10_LIBRARY})
        endif()
endif()

if(HOST_BUILD AND ENABLE_FREEDV_NATIVE_CODEC AND ENABLE_FREEDV_USB_TEST)
        add_executable(freedv_usb_audio_test tools/freedv/usb_audio_integration_test.c src/freedv/usb_audio.c src/freedv/usb_headsets.c
                       src/freedv/usb_evidence_store.c)
        target_compile_options(freedv_usb_audio_test PRIVATE -UNDEBUG)
        target_compile_definitions(freedv_usb_audio_test PRIVATE
                HOST_BUILD=1 FREEDV_BUILD_ID="${FREEDV_BUILD_ID}" FREEDV_SOURCE_COMMIT="${FREEDV_SOURCE_COMMIT}")
        target_include_directories(freedv_usb_audio_test PRIVATE ${FREEDV_SAMPLERATE_INCLUDE_DIR})
        target_link_libraries(freedv_usb_audio_test PRIVATE freedv_native_codec pulse)
endif()

if(ENABLE_FREEDV_DIAGNOSTICS OR ENABLE_FREEDV_VOICE)
        find_package(Threads REQUIRED)
        add_library(freedv_voice_session STATIC src/freedv/voice_session.c src/freedv/qualified_profile.c src/freedv/watchdog.c src/freedv/native_trace.c)
        target_link_libraries(freedv_voice_session PUBLIC Threads::Threads)
        add_library(freedv_writer_gate STATIC src/freedv/writer_gate.c src/freedv/route_trace.c)
        target_link_libraries(freedv_writer_gate PUBLIC Threads::Threads)
        # This state prototype does not access the radio and is not a diagnostic UI.
        add_library(freedv_diagnostic_state STATIC tools/freedv/diagnostic_state.c)
        target_include_directories(freedv_diagnostic_state PUBLIC tools/freedv)
        if(HOST_BUILD AND ENABLE_FREEDV_DIAGNOSTICS)
                enable_testing()
                add_executable(freedv_usb_panel_test tools/freedv/usb_panel_test.c)
                target_compile_options(freedv_usb_panel_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_usb_panel COMMAND freedv_usb_panel_test)
                add_executable(freedv_usb_audio_levels_test tools/freedv/usb_audio_levels_test.c)
                target_compile_options(freedv_usb_audio_levels_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_usb_audio_levels_test PRIVATE m)
                add_test(NAME freedv_usb_audio_levels COMMAND freedv_usb_audio_levels_test)
                add_executable(freedv_usb_ui_test tools/freedv/usb_ui_test.c tools/freedv/usb_ui_cfg_test.cpp
                    src/app_window.c src/control_owner.c src/fonts/sony_14.c src/fonts/sony_18.c src/fonts/sony_22.c)
                target_include_directories(freedv_usb_ui_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src)
                target_compile_definitions(freedv_usb_ui_test PRIVATE HOST_BUILD=1 ENABLE_FREEDV_VOICE=1 ENABLE_FREEDV_USB_VOICE=1 ENABLE_FREEDV_USB_TEST=1)
                target_compile_options(freedv_usb_ui_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_usb_ui_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_usb_ui_test PRIVATE cfg freedv_writer_gate Threads::Threads m)
                add_test(NAME freedv_usb_ui COMMAND freedv_usb_ui_test)
                add_executable(freedv_usb_navigation_test tools/freedv/usb_navigation_test.c src/freedv/usb_navigation.c)
                target_include_directories(freedv_usb_navigation_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src)
                target_compile_definitions(freedv_usb_navigation_test PRIVATE ENABLE_FREEDV_USB_VOICE=1 ENABLE_FREEDV_VOICE=1)
                target_compile_options(freedv_usb_navigation_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_usb_navigation COMMAND freedv_usb_navigation_test)
                add_executable(freedv_usb_headsets_test tools/freedv/usb_headsets_test.c src/freedv/usb_headsets.c)
                target_compile_options(freedv_usb_headsets_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_usb_headsets_test PRIVATE pulse Threads::Threads)
                add_test(NAME freedv_usb_headsets COMMAND freedv_usb_headsets_test)
                add_executable(freedv_usb_evidence_store_test tools/freedv/usb_evidence_store_test.c
                               src/freedv/usb_evidence_store.c)
                target_compile_options(freedv_usb_evidence_store_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_usb_evidence_store COMMAND freedv_usb_evidence_store_test)
                find_package(PkgConfig REQUIRED)
                pkg_check_modules(FREEDV_USB_RENDER_SAMPLERATE REQUIRED IMPORTED_TARGET samplerate)
                add_executable(freedv_usb_render_budget_test tools/freedv/usb_render_budget_test.c)
                target_include_directories(freedv_usb_render_budget_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_usb_render_budget_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_usb_render_budget_test PRIVATE PkgConfig::FREEDV_USB_RENDER_SAMPLERATE m)
                add_test(NAME freedv_usb_render_budget COMMAND freedv_usb_render_budget_test)
                find_package(Python3 COMPONENTS Interpreter REQUIRED)
                add_test(NAME freedv_usb_navigation_service COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_navigation_service.py)
                add_test(NAME freedv_usb_test_support COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_test_support.py)
                add_test(NAME freedv_usb_output_contract COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_output_contract.py)
                add_test(NAME freedv_usb_output_contract_optimized COMMAND ${Python3_EXECUTABLE} -O
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_output_contract.py)
                add_test(NAME freedv_usb_capture_open_contract COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_capture_open_contract.py)
                add_test(NAME freedv_usb_capture_open_contract_optimized COMMAND ${Python3_EXECUTABLE} -O
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_capture_open_contract.py)
                add_test(NAME freedv_usb_fifo_clock COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_fifo_clock.py)
                add_test(NAME freedv_usb_backend_observer COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_backend_observer.py)
                add_test(NAME freedv_usb_backend_observer_optimized COMMAND ${Python3_EXECUTABLE} -O
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_backend_observer.py)
                set_tests_properties(freedv_usb_output_contract freedv_usb_output_contract_optimized
                    freedv_usb_fifo_clock freedv_usb_backend_observer
                    freedv_usb_backend_observer_optimized PROPERTIES TIMEOUT 30)
                add_test(NAME freedv_usb_test_campaign COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_campaign.py)
                add_test(NAME freedv_usb_host_resources COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_host_resources.py)
                add_test(NAME freedv_usb_controller_build COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_controller_build.py)
                add_test(NAME freedv_usb_ci_diagnostics COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_usb_ci_diagnostics.py)
                add_executable(freedv_native_contract_test tools/freedv/native_contract_test.c)
                target_compile_options(freedv_native_contract_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_native_contract COMMAND freedv_native_contract_test)
                add_executable(freedv_voice_validation_test tools/freedv/voice_validation_test.c)
                target_compile_options(freedv_voice_validation_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_voice_validation_test PRIVATE freedv_voice_session)
                add_test(NAME freedv_voice_validation COMMAND freedv_voice_validation_test)
                add_test(NAME freedv_native_trace COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_native_trace.py)
                add_test(NAME freedv_native_conversation_evidence COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_assess_native_conversation.py)
                add_test(NAME freedv_baseband_inventory COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/baseband/test_inventory_firmware.py)
                add_test(NAME freedv_baseband_decryption COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/baseband/test_decrypt_xgf_payload.py)
                add_test(NAME freedv_candidate_bundle COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_voice_candidate_bundle.py)
                add_test(NAME freedv_retest_collection COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_radio_retest.py)
                add_test(NAME freedv_capture_source_watch COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_capture_source_watch.py)
                add_test(NAME freedv_capture_continuity COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tools/freedv/test_capture_continuity.py)
                add_executable(freedv_route_driver_test tools/freedv/route_driver_test.c)
                target_include_directories(freedv_route_driver_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_route_driver_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_route_driver COMMAND freedv_route_driver_test)
                add_executable(freedv_route_driver_blocked_test tools/freedv/route_driver_test.c)
                target_include_directories(freedv_route_driver_blocked_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_definitions(freedv_route_driver_blocked_test PRIVATE BOARD_X6200=1)
                target_compile_options(freedv_route_driver_blocked_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_route_driver_blocked COMMAND freedv_route_driver_blocked_test)
                add_executable(freedv_agent_protocol_test tools/freedv/agent_protocol_test.c)
                target_include_directories(freedv_agent_protocol_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_agent_protocol_test PRIVATE -UNDEBUG)
                add_test(NAME freedv_agent_protocol COMMAND freedv_agent_protocol_test)
                add_executable(freedv_route_trace_test tools/freedv/route_trace_test.c)
                target_include_directories(freedv_route_trace_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_route_trace_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_route_trace_test PRIVATE freedv_writer_gate)
                add_test(NAME freedv_route_trace COMMAND freedv_route_trace_test)
                add_test(NAME freedv_route_agent COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tools/freedv/test_route_agent.py)
                add_executable(freedv_voice_writer_test tools/freedv/voice_writer_test.c)
                target_include_directories(freedv_voice_writer_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_voice_writer_test PRIVATE -UNDEBUG)
                target_compile_definitions(freedv_voice_writer_test PRIVATE ENABLE_FREEDV_VOICE=1)
                target_link_libraries(freedv_voice_writer_test PRIVATE freedv_writer_gate)
                add_test(NAME freedv_voice_writer COMMAND freedv_voice_writer_test)
                add_executable(freedv_voice_bookmarks_test tools/freedv/voice_bookmarks_test.c src/params/migrations.c)
                target_compile_options(freedv_voice_bookmarks_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_voice_bookmarks_test PRIVATE cfg)
                add_dependencies(freedv_voice_bookmarks_test params_sqlite)
                add_test(NAME freedv_voice_bookmarks COMMAND freedv_voice_bookmarks_test ${CMAKE_BINARY_DIR}/params.db)
                add_executable(freedv_voice_audio_test tools/freedv/voice_audio_test.c)
                target_compile_definitions(freedv_voice_audio_test PRIVATE BOARD_X6200=1)
                target_compile_options(freedv_voice_audio_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_voice_audio_test PRIVATE Threads::Threads)
                add_test(NAME freedv_voice_audio COMMAND freedv_voice_audio_test)
                add_executable(freedv_voice_session_test tools/freedv/voice_session_test.c)
                target_compile_options(freedv_voice_session_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_voice_session_test PRIVATE freedv_voice_session)
                add_test(NAME freedv_voice_session COMMAND freedv_voice_session_test)
                add_test(NAME freedv_voice_timeout COMMAND freedv_voice_session_test --timeout)
                set_tests_properties(freedv_voice_timeout PROPERTIES TIMEOUT 40)
                find_package(Threads REQUIRED)
                foreach(fence IN ITEMS input_fence front_input remote_input_fence)
                    add_executable(freedv_${fence}_test tools/freedv/${fence}_test.c src/control_owner.c)
                    target_include_directories(freedv_${fence}_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                        ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                    target_compile_definitions(freedv_${fence}_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                    target_compile_options(freedv_${fence}_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                    target_link_options(freedv_${fence}_test PRIVATE -Wl,--gc-sections)
                    target_link_libraries(freedv_${fence}_test PRIVATE freedv_writer_gate Threads::Threads)
                    add_test(NAME freedv_${fence} COMMAND freedv_${fence}_test)
                endforeach()
                find_package(PkgConfig REQUIRED)
                pkg_check_modules(FREEDV_BACKEND_DEPS REQUIRED IMPORTED_TARGET libnm dbus-glib-1)
                add_executable(freedv_voice_radio_test tools/freedv/voice_radio_test.c src/hw/stub/hw_stub.c
                    src/control_owner.c src/freedv/packet_tx.c)
                target_include_directories(freedv_voice_radio_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_voice_radio_test PRIVATE ENABLE_FREEDV_VOICE=1 FREEDV_HOST_TEST_PROFILE=1)
                target_compile_options(freedv_voice_radio_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_voice_radio_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_voice_radio_test PRIVATE freedv_writer_gate freedv_voice_session freedv_diagnostic_state Threads::Threads m PkgConfig::FREEDV_BACKEND_DEPS lvgl)
                add_test(NAME freedv_voice_radio COMMAND freedv_voice_radio_test)
                add_executable(freedv_native_coordinator_test tools/freedv/native_coordinator_test.c
                    tools/freedv/session_controller.c src/freedv/watchdog.c)
                target_include_directories(freedv_native_coordinator_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                    ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_native_coordinator_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_native_coordinator_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_native_coordinator_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_native_coordinator_test PRIVATE freedv_diagnostic_state Threads::Threads m)
                target_link_libraries(freedv_native_coordinator_test PRIVATE freedv_writer_gate)
                add_test(NAME freedv_native_coordinator COMMAND freedv_native_coordinator_test)
                add_executable(freedv_native_radio_backend_test tools/freedv/native_radio_backend_test.c
                    src/hw/stub/hw_stub.c src/control_owner.c src/freedv/packet_tx.c)
                target_include_directories(freedv_native_radio_backend_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                    ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_native_radio_backend_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_native_radio_backend_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_native_radio_backend_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_native_radio_backend_test PRIVATE freedv_writer_gate freedv_diagnostic_state
                    Threads::Threads m PkgConfig::FREEDV_BACKEND_DEPS)
                add_test(NAME freedv_native_radio_backend COMMAND freedv_native_radio_backend_test)
                add_executable(freedv_native_radio_blocked_test tools/freedv/native_radio_backend_test.c
                    src/hw/stub/hw_stub.c src/control_owner.c src/freedv/packet_tx.c)
                target_include_directories(freedv_native_radio_blocked_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                    ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_native_radio_blocked_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1 BOARD_X6200=1)
                target_compile_options(freedv_native_radio_blocked_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_native_radio_blocked_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_native_radio_blocked_test PRIVATE freedv_writer_gate freedv_diagnostic_state
                    Threads::Threads m PkgConfig::FREEDV_BACKEND_DEPS)
                add_test(NAME freedv_native_radio_blocked COMMAND freedv_native_radio_blocked_test)
                add_executable(freedv_native_audio_adapter_test tools/freedv/native_audio_adapter_test.c
                    src/freedv/native_audio.c src/freedv/capture_dispatch.c src/freedv/capture_ring.c src/freedv/wav_capture.c)
                target_include_directories(freedv_native_audio_adapter_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_definitions(freedv_native_audio_adapter_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_native_audio_adapter_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_native_audio_adapter_test PRIVATE Threads::Threads m)
                add_test(NAME freedv_native_audio_adapter COMMAND freedv_native_audio_adapter_test)
                add_executable(freedv_audio_stereo_test tools/freedv/audio_stereo_test.c src/freedv/capture_ring.c)
                target_include_directories(freedv_audio_stereo_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_audio_stereo_test PRIVATE BOARD_X6200=1 ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_audio_stereo_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_audio_stereo_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_audio_stereo_test PRIVATE Threads::Threads freedv_writer_gate)
                add_test(NAME freedv_audio_stereo COMMAND freedv_audio_stereo_test)
                if(ENABLE_FREEDV_NATIVE_CODEC)
                    add_executable(freedv_voice_fault_test tools/freedv/voice_fault_test.c src/freedv/voice_engine.c)
                    target_compile_definitions(freedv_voice_fault_test PRIVATE FREEDV_VOICE_TEST_HOOKS=1 RADE_ACQ_PARALLEL=1)
                    get_target_property(FREEDV_CODEC_INCLUDES freedv_native_codec INCLUDE_DIRECTORIES)
                    target_include_directories(freedv_voice_fault_test PRIVATE ${FREEDV_CODEC_INCLUDES})
                    target_compile_options(freedv_voice_fault_test PRIVATE -UNDEBUG -include ${FREEDV_OPUS_BUILD}/config.h)
                    target_link_libraries(freedv_voice_fault_test PRIVATE ${FREEDV_RADE_LIBRARY} ${FREEDV_OPUS_LIBRARY}
                        ${FREEDV_SAMPLERATE_LIBRARY} Threads::Threads m)
                    add_test(NAME freedv_voice_faults COMMAND freedv_voice_fault_test)
                    add_executable(freedv_voice_runner tools/freedv/voice_runner.c)
                    target_compile_options(freedv_voice_runner PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_voice_runner PRIVATE freedv_native_codec)
                    add_executable(freedv_voice_engine_test tools/freedv/voice_engine_test.c)
                    target_compile_options(freedv_voice_engine_test PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_voice_engine_test PRIVATE freedv_native_codec)
                    add_test(NAME freedv_voice_engine COMMAND freedv_voice_engine_test --turns 100)
                    add_test(NAME freedv_voice_engine_reopen_warm COMMAND freedv_voice_engine_test --turns 1 --cycles 2 --rate 48000)
                    add_executable(freedv_native_audio_codec_adapter_test tools/freedv/native_audio_adapter_test.c
                        src/freedv/native_audio.c src/freedv/capture_dispatch.c src/freedv/capture_ring.c src/freedv/wav_capture.c)
                    target_include_directories(freedv_native_audio_codec_adapter_test PRIVATE ${CMAKE_SOURCE_DIR} ${FREEDV_SAMPLERATE_INCLUDE_DIR})
                    target_compile_definitions(freedv_native_audio_codec_adapter_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1 ENABLE_FREEDV_NATIVE_CODEC=1)
                    target_compile_options(freedv_native_audio_codec_adapter_test PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_native_audio_codec_adapter_test PRIVATE freedv_native_codec Threads::Threads m)
                    add_executable(freedv_persistent_case_test tools/freedv/native_audio_adapter_test.c
                        src/freedv/native_audio.c src/freedv/voice_audio.c src/freedv/capture_dispatch.c
                        src/freedv/capture_ring.c src/freedv/wav_capture.c)
                    target_include_directories(freedv_persistent_case_test PRIVATE ${CMAKE_SOURCE_DIR} ${FREEDV_SAMPLERATE_INCLUDE_DIR})
                    target_compile_definitions(freedv_persistent_case_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1
                        ENABLE_FREEDV_NATIVE_CODEC=1 ENABLE_FREEDV_VOICE=1 FREEDV_HOST_TEST_PROFILE=1)
                    target_compile_options(freedv_persistent_case_test PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_persistent_case_test PRIVATE freedv_native_codec Threads::Threads m)
                    add_test(NAME freedv_persistent_cases COMMAND freedv_persistent_case_test
                        ${CMAKE_SOURCE_DIR}/tools/freedv/fixtures/rade-v1-reference-s16le.raw)
                    add_executable(freedv_native_stream_fixture_test tools/freedv/rade_stream_fixture_test.c)
                    target_include_directories(freedv_native_stream_fixture_test PRIVATE ${FREEDV_SAMPLERATE_INCLUDE_DIR})
                    target_compile_options(freedv_native_stream_fixture_test PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_native_stream_fixture_test PRIVATE freedv_native_codec)
                    add_executable(freedv_native_stream_test tools/freedv/rade_stream_test.c)
                    target_compile_options(freedv_native_stream_test PRIVATE -UNDEBUG)
                    target_link_libraries(freedv_native_stream_test PRIVATE freedv_native_codec)
                    add_test(NAME freedv_native_stream COMMAND freedv_native_stream_test --unpaced)
                endif()
                add_executable(freedv_writer_preferences_test tools/freedv/writer_preferences_test.cpp)
                target_compile_definitions(freedv_writer_preferences_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_writer_preferences_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_writer_preferences_test PRIVATE cfg freedv_writer_gate)
                add_test(NAME freedv_writer_preferences COMMAND freedv_writer_preferences_test)
                add_executable(freedv_capture_dispatch_test src/freedv/capture_dispatch.c src/freedv/capture_ring.c
                               tools/freedv/capture_dispatch_test.c)
                target_include_directories(freedv_capture_dispatch_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_definitions(freedv_capture_dispatch_test PRIVATE ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_capture_dispatch_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_capture_dispatch_test PRIVATE Threads::Threads)
                add_test(NAME freedv_capture_dispatch COMMAND freedv_capture_dispatch_test)
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
                target_link_libraries(freedv_audio_capture_test PRIVATE Threads::Threads freedv_writer_gate)
                add_test(NAME freedv_audio_capture COMMAND freedv_audio_capture_test)
                add_executable(freedv_audio_playback_test tools/freedv/audio_playback_test.c)
                target_include_directories(freedv_audio_playback_test PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_audio_playback_test PRIVATE BOARD_X6200=1 ENABLE_FREEDV_DIAGNOSTICS=1)
                target_compile_options(freedv_audio_playback_test PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_audio_playback_test PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_audio_playback_test PRIVATE Threads::Threads freedv_writer_gate)
                add_test(NAME freedv_audio_playback COMMAND freedv_audio_playback_test)
                pkg_check_modules(FREEDV_PULSE_PROBE REQUIRED IMPORTED_TARGET libpulse)
                add_executable(freedv_playback_frame_probe tools/freedv/playback_frame_probe.c)
                target_include_directories(freedv_playback_frame_probe PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_options(freedv_playback_frame_probe PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_playback_frame_probe PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_playback_frame_probe PRIVATE PkgConfig::FREEDV_PULSE_PROBE Threads::Threads m)
                add_executable(freedv_pulse_playback_probe tools/freedv/pulse_playback_probe.c)
                target_include_directories(freedv_pulse_playback_probe PRIVATE ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/src
                                           ${CMAKE_SOURCE_DIR}/build-host/deps/include)
                target_compile_definitions(freedv_pulse_playback_probe PRIVATE BOARD_X6200=1 ENABLE_FREEDV_DIAGNOSTICS=1 ENABLE_FREEDV_VOICE=1)
                target_compile_options(freedv_pulse_playback_probe PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
                target_link_options(freedv_pulse_playback_probe PRIVATE -Wl,--gc-sections)
                target_link_libraries(freedv_pulse_playback_probe PRIVATE PkgConfig::FREEDV_PULSE_PROBE Threads::Threads m)
                add_executable(freedv_packet_tx_test src/freedv/packet_tx.c tools/freedv/packet_tx_test.c)
                target_include_directories(freedv_packet_tx_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_packet_tx_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_packet_tx_test PRIVATE Threads::Threads)
                add_test(NAME freedv_packet_tx COMMAND freedv_packet_tx_test)
                add_executable(freedv_usb_radio_gate_test tools/freedv/usb_radio_gate_test.c src/freedv/packet_tx.c)
                target_include_directories(freedv_usb_radio_gate_test PRIVATE ${CMAKE_SOURCE_DIR})
                target_compile_options(freedv_usb_radio_gate_test PRIVATE -UNDEBUG)
                target_link_libraries(freedv_usb_radio_gate_test PRIVATE Threads::Threads m)
                add_test(NAME freedv_usb_radio_gate COMMAND freedv_usb_radio_gate_test)
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
                target_link_libraries(freedv_control_lease_test PRIVATE Threads::Threads freedv_writer_gate)
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
                        target_link_libraries(freedv_x6200_route_test PRIVATE freedv_writer_gate)
                        add_test(NAME freedv_x6200_route_snapshot COMMAND freedv_x6200_route_test)
                endif()
        endif()
endif()

# Headers are included from the top: lv_conf.h, lvgl/, src/...
if(ENABLE_FREEDV_NATIVE_CODEC AND NOT TARGET freedv_voice_runner)
        add_executable(freedv_voice_runner tools/freedv/voice_runner.c)
        target_link_libraries(freedv_voice_runner PRIVATE freedv_native_codec)
        set_property(TARGET freedv_voice_runner PROPERTY INSTALL_RPATH /opt/freedv-benchmark/lib)
endif()
if(ENABLE_FREEDV_NATIVE_CODEC AND ENABLE_FREEDV_VOICE)
        # Runs against a separate private PA server with virtual devices only.
        # HOST_BUILD here selects the null radio/mixer profile, including ARM.
        add_executable(freedv_voice_pulse_probe tools/freedv/voice_pulse_probe.c
            src/freedv/native_audio.c src/freedv/voice_audio.c src/freedv/capture_dispatch.c
            src/freedv/capture_ring.c src/freedv/wav_capture.c)
        target_include_directories(freedv_voice_pulse_probe PRIVATE ${CMAKE_SOURCE_DIR}
            ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/build-host/deps/include ${FREEDV_SAMPLERATE_INCLUDE_DIR})
        target_compile_definitions(freedv_voice_pulse_probe PRIVATE HOST_BUILD=1 LV_DRAW_NEON=0
            ENABLE_FREEDV_DIAGNOSTICS=1 ENABLE_FREEDV_NATIVE_CODEC=1 ENABLE_FREEDV_VOICE=1 FREEDV_HOST_TEST_PROFILE=1)
        target_compile_options(freedv_voice_pulse_probe PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
        target_link_options(freedv_voice_pulse_probe PRIVATE -Wl,--gc-sections)
        target_link_libraries(freedv_voice_pulse_probe PRIVATE freedv_native_codec pulse asound Threads::Threads m)
        set_property(TARGET freedv_voice_pulse_probe PROPERTY INSTALL_RPATH /opt/freedv-benchmark/lib)
endif()
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
if(ENABLE_FREEDV_USB_VOICE AND NOT HOST_BUILD AND NOT HW_BACKEND STREQUAL "x6200")
        message(FATAL_ERROR "USB headset FreeDV is restricted to X6200")
endif()
if(FREEDV_RETEST_20261102 AND NOT HOST_BUILD AND NOT HW_BACKEND STREQUAL "x6200")
        message(FATAL_ERROR "Reviewed retest routes apply only to X6200")
endif()
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

if(HOST_BUILD AND ENABLE_FREEDV_DIAGNOSTICS)
        # Ownership tests share the cross-process settings lease used by the
        # web configurator. Parallel execution would test each other's leases.
        get_property(FREEDV_CASES DIRECTORY PROPERTY TESTS)
        set_tests_properties(${FREEDV_CASES} PROPERTIES RESOURCE_LOCK freedv_settings_lease)
endif()
