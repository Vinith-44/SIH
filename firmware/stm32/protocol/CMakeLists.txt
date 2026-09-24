# Host build of the portable protocol library + its unit tests.
#
#   cmake -S firmware/stm32/protocol -B build/proto -G Ninja
#   cmake --build build/proto
#   ctest --test-dir build/proto --output-on-failure
#
# The same sources are compiled into the STM32 firmware (firmware/stm32), so a
# test that passes here is a test of the code that runs on the board.
cmake_minimum_required(VERSION 3.20)
project(sm_proto C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

option(SM_SANITIZE "Build the tests with AddressSanitizer + UBSan" OFF)

add_library(sm_proto STATIC src/sm_proto.c src/sm_cobs_crc.c)
target_include_directories(sm_proto PUBLIC include)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  # -Wconversion matters: the MCU has 16/32-bit types everywhere and a silent
  # narrowing is exactly the kind of bug that only shows up on the board.
  target_compile_options(sm_proto PRIVATE -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic)
endif()

enable_testing()
add_executable(test_sm_proto tests/test_sm_proto.c)
target_link_libraries(test_sm_proto PRIVATE sm_proto)
target_include_directories(test_sm_proto PRIVATE tests)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(test_sm_proto PRIVATE -Wall -Wextra -Werror)
endif()
if(SM_SANITIZE)
  foreach(t sm_proto test_sm_proto)
    target_compile_options(${t} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
    target_link_options(${t} PRIVATE -fsanitize=address,undefined)
  endforeach()
endif()
add_test(NAME sm_proto COMMAND test_sm_proto)
