# Toolchain file: STM32F103C8 (Cortex-M3) with the GNU Arm Embedded toolchain.
#   cmake -S firmware/stm32 -B build/fw -G Ninja -DCMAKE_TOOLCHAIN_FILE=firmware/stm32/cmake/arm-none-eabi.cmake
# Set ARM_TOOLCHAIN_DIR (the folder with arm-none-eabi-gcc) if it is not on PATH.
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

set(ARM_TOOLCHAIN_DIR "$ENV{ARM_TOOLCHAIN_DIR}" CACHE PATH "folder containing arm-none-eabi-gcc")
if(ARM_TOOLCHAIN_DIR)
  set(_prefix "${ARM_TOOLCHAIN_DIR}/arm-none-eabi-")
else()
  set(_prefix "arm-none-eabi-")
endif()
if(CMAKE_HOST_WIN32)
  set(_exe ".exe")
else()
  set(_exe "")
endif()

set(CMAKE_C_COMPILER   "${_prefix}gcc${_exe}")
set(CMAKE_ASM_COMPILER "${_prefix}gcc${_exe}")
set(CMAKE_OBJCOPY      "${_prefix}objcopy${_exe}" CACHE FILEPATH "")
set(CMAKE_SIZE         "${_prefix}size${_exe}" CACHE FILEPATH "")

# A bare-metal compiler cannot link a test program without our linker script.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

set(MCU_FLAGS "-mcpu=cortex-m3 -mthumb -mfloat-abi=soft")
set(CMAKE_C_FLAGS_INIT   "${MCU_FLAGS} -ffunction-sections -fdata-sections -fno-common")
set(CMAKE_ASM_FLAGS_INIT "${MCU_FLAGS} -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${MCU_FLAGS} -specs=nano.specs -specs=nosys.specs -Wl,--gc-sections")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
