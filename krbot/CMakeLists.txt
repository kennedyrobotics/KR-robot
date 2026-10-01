cmake_minimum_required(VERSION 3.20)
project(krbot VERSION 0.1.0 LANGUAGES CXX)

# KR-bot 5-layer C++ stack — see docs/system-design.md and Design doc v3
# (Design/knowledge-agent-architecture.md). Built natively on the BeagleY-AI.

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

option(KRBOT_BUILD_TESTS "Build unit tests (GoogleTest)" ON)
# Phase 1 (doc v3 §10): fetched with FetchContent when switched on. Stubs are used until then.
option(KRBOT_WITH_CLIPS "Embed CLIPS 6.4 for the L4 reasoner" OFF)
option(KRBOT_WITH_BTCPP "Use BehaviorTree.CPP for the L5 executor" OFF)

add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion)

find_package(Threads REQUIRED)

# ---- layers: one static library each, dependencies only point downward or to common ----
add_library(krbot_common STATIC
  src/common/Config.cpp
  src/common/Log.cpp)
target_include_directories(krbot_common PUBLIC src)
target_link_libraries(krbot_common PUBLIC Threads::Threads)

# L1 Driver / Controller
add_library(krbot_driver STATIC
  src/driver/SerialPort.cpp
  src/driver/I2CDevice.cpp
  src/driver/YahboomProtocol.cpp
  src/driver/YahboomMotorControllerDriver.cpp
  src/driver/SimMotorController.cpp
  src/driver/DriverRegistry.cpp)
target_link_libraries(krbot_driver PUBLIC krbot_common)

# L3 Knowledge / World Model (built before L2 because L2 maps observations into facts)
add_library(krbot_knowledge STATIC
  src/knowledge/FactTable.cpp
  src/knowledge/FactStore.cpp)
target_link_libraries(krbot_knowledge PUBLIC krbot_common)

# L2 Perception / Sensing
add_library(krbot_percep STATIC
  src/percep/ObservationMapper.cpp
  src/percep/PerceptionLoop.cpp
  src/percep/MotorHealthSource.cpp)
target_link_libraries(krbot_percep PUBLIC krbot_driver krbot_knowledge)

# L4 Reasoning / Planning
add_library(krbot_reason STATIC
  src/reason/NullReasoner.cpp
  src/reason/ReasoningLoop.cpp)
target_link_libraries(krbot_reason PUBLIC krbot_knowledge)
if(KRBOT_WITH_CLIPS)
  message(FATAL_ERROR "KRBOT_WITH_CLIPS: ClipsEngine lands in Phase 1 — not wired yet")
endif()

# L5 Execution & Operator Interface
add_library(krbot_exec STATIC
  src/exec/InputDriver.cpp
  src/exec/TeleopSafety.cpp
  src/exec/ManualDrive.cpp
  src/exec/Watchdog.cpp
  src/exec/GoalArbiter.cpp
  src/exec/StubExecutor.cpp)
target_link_libraries(krbot_exec PUBLIC krbot_driver krbot_knowledge krbot_reason)
if(KRBOT_WITH_BTCPP)
  message(FATAL_ERROR "KRBOT_WITH_BTCPP: BehaviorTree.CPP executor lands in Phase 1 — not wired yet")
endif()

# core: wiring + main()
add_executable(krbot src/core/App.cpp src/core/main.cpp)
target_link_libraries(krbot PRIVATE krbot_exec krbot_percep krbot_reason krbot_knowledge krbot_driver)

install(TARGETS krbot RUNTIME DESTINATION bin)

if(KRBOT_BUILD_TESTS)
  find_package(GTest)
  if(GTest_FOUND)
    enable_testing()
    add_executable(krbot_tests
      tests/test_queue.cpp
      tests/test_config.cpp
      tests/test_yahboom_protocol.cpp
      tests/test_teleop_safety.cpp
      tests/test_axis.cpp
      tests/test_fact_table.cpp
      tests/test_goal_arbiter.cpp
      tests/test_watchdog.cpp
      tests/test_manual_drive.cpp)
    target_link_libraries(krbot_tests PRIVATE krbot_exec krbot_percep krbot_reason krbot_knowledge
                          krbot_driver GTest::gtest_main)
    include(GoogleTest)
    gtest_discover_tests(krbot_tests)
  else()
    message(WARNING "GoogleTest not found (apt install libgtest-dev) — tests disabled")
  endif()
endif()
