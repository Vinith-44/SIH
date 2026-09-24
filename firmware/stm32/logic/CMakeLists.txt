# Host build of every portable firmware module + unit tests (protocol and logic).
#
#   cmake -S firmware/stm32/logic -B build/host
#   cmake --build build/host
#   ctest --test-dir build/host --output-on-failure
cmake_minimum_required(VERSION 3.20)
project(sm_host_tests C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

option(SM_SANITIZE "Build the tests with AddressSanitizer + UBSan" OFF)
enable_testing()

add_subdirectory(../protocol protocol)

add_library(sm_logic STATIC src/sm_logic.c)
target_include_directories(sm_logic PUBLIC include)
target_link_libraries(sm_logic PUBLIC sm_proto)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(sm_logic PRIVATE -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic)
endif()

add_executable(test_sm_logic tests/test_sm_logic.c)
target_link_libraries(test_sm_logic PRIVATE sm_logic)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(test_sm_logic PRIVATE -Wall -Wextra -Werror)
endif()
if(SM_SANITIZE)
  foreach(t sm_logic test_sm_logic)
    target_compile_options(${t} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
    target_link_options(${t} PRIVATE -fsanitize=address,undefined)
  endforeach()
endif()
add_test(NAME sm_logic COMMAND test_sm_logic)
