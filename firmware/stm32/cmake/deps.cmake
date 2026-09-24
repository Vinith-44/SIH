# Third-party sources, downloaded at configure time and pinned by SHA-256.
#
# Nothing from ST or FreeRTOS is copied into our repo: the same versions
# STM32CubeMX would place in Drivers/ and Middlewares/ are fetched here, so a
# fresh clone (or CI) builds without CubeMX installed.
#   * CMSIS device + HAL: STM32CubeF1 v1.8.7 submodules (BSD-3-Clause)
#   * FreeRTOS 10.6.2 + ST's CMSIS-RTOS2 wrapper (MIT)
#   * CMSIS core headers and cmsis_os2.h from STM32CubeF1 v1.8.7 (Apache-2.0)
include(FetchContent)
set(FETCHCONTENT_QUIET ON)

FetchContent_Declare(cmsis_device_f1
  URL https://github.com/STMicroelectronics/cmsis_device_f1/archive/refs/tags/v4.3.5.tar.gz
  URL_HASH SHA256=2994ffe58af1f819928f11840359565e0293e91dfb840661da77c2e02de24ca3
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(stm32f1_hal
  URL https://github.com/STMicroelectronics/stm32f1xx_hal_driver/archive/refs/tags/v1.1.10.tar.gz
  URL_HASH SHA256=dac985f582b763e8a54aee8329fc7cc86e5690d4ed97f48f7bb7a89eb23f94ca
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(freertos
  URL https://github.com/STMicroelectronics/stm32-mw-freertos/archive/refs/tags/v10.6.2_20241011.tar.gz
  URL_HASH SHA256=bba8f649bd801f06b3f8963251ed39e43aad3728467c207e6c3deb2b6f8a29d7
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(cmsis_device_f1 stm32f1_hal freertos)

# Single CMSIS headers (the full CMSIS repo is ~90 MB for five files).
set(CMSIS_CORE_DIR "${CMAKE_BINARY_DIR}/_deps/cmsis_core/Include")
set(CMSIS_RTOS2_DIR "${CMAKE_BINARY_DIR}/_deps/cmsis_core/RTOS2")
set(_cube "https://raw.githubusercontent.com/STMicroelectronics/STM32CubeF1/v1.8.7/Drivers/CMSIS")
foreach(entry
    "Include/core_cm3.h|655bb08e9474438dec8b1874ea6c63749dc2787b85f06a0987c4870bcb580f21"
    "Include/cmsis_gcc.h|341982e6d6c91119214c47d7a04b31f1f8ce0970c45e2c9fe0870c80d0876e28"
    "Include/cmsis_compiler.h|03ab80c426a8360eaaad833ac816462a9f38cb2e4dbdc490914880ad071181fe"
    "Include/cmsis_version.h|c2570d56ee85d4f2ec0fb62f31d080d3cddf3a015500a4ed5a006a571637b43c"
    "Include/mpu_armv7.h|e4968e627df085e4ebf46be5140be657e035d978ae1938c1c2e3ee49fbde5030"
    "RTOS2/Include/cmsis_os2.h|9ad88770c156ac4e7a84775a95708620551c64e1bf9477fbe197e7a2e7fc8d94"
    "RTOS2/Include/os_tick.h|f76bcfd977c1fce91c1368986ce2582c4d2c15b767c2014a8cfbc796225e6af3")
  string(REPLACE "|" ";" parts "${entry}")
  list(GET parts 0 rel)
  list(GET parts 1 sha)
  get_filename_component(name "${rel}" NAME)
  if(rel MATCHES "^RTOS2")
    set(dest "${CMSIS_RTOS2_DIR}/${name}")
  else()
    set(dest "${CMSIS_CORE_DIR}/${name}")
  endif()
  if(NOT EXISTS "${dest}")
    file(DOWNLOAD "${_cube}/${rel}" "${dest}" EXPECTED_HASH SHA256=${sha} STATUS st)
    list(GET st 0 code)
    if(NOT code EQUAL 0)
      message(FATAL_ERROR "download of ${rel} failed: ${st}")
    endif()
  endif()
endforeach()

set(CMSIS_DEVICE_DIR "${cmsis_device_f1_SOURCE_DIR}")
set(HAL_DIR "${stm32f1_hal_SOURCE_DIR}")
set(FREERTOS_DIR "${freertos_SOURCE_DIR}/Source")
