# StoreMind sensor node firmware: STM32F103C8 + FreeRTOS (CMSIS-RTOS2).
#
#   cmake -S firmware/stm32 -B build/fw -G Ninja \
#         -DCMAKE_TOOLCHAIN_FILE=firmware/stm32/cmake/arm-none-eabi.cmake -DCMAKE_BUILD_TYPE=MinSizeRel
#   cmake --build build/fw          # -> storemind_node.elf / .hex / .bin + size report
#
# ST's HAL, CMSIS and FreeRTOS are downloaded at configure time (cmake/deps.cmake),
# pinned by SHA-256.  The host unit tests are a separate project: firmware/stm32/logic.
cmake_minimum_required(VERSION 3.20)
project(storemind_node C ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE MinSizeRel)
endif()

include(cmake/deps.cmake)

set(HAL_SOURCES
  ${HAL_DIR}/Src/stm32f1xx_hal.c
  ${HAL_DIR}/Src/stm32f1xx_hal_cortex.c
  ${HAL_DIR}/Src/stm32f1xx_hal_dma.c
  ${HAL_DIR}/Src/stm32f1xx_hal_exti.c
  ${HAL_DIR}/Src/stm32f1xx_hal_flash.c
  ${HAL_DIR}/Src/stm32f1xx_hal_flash_ex.c
  ${HAL_DIR}/Src/stm32f1xx_hal_gpio.c
  ${HAL_DIR}/Src/stm32f1xx_hal_gpio_ex.c
  ${HAL_DIR}/Src/stm32f1xx_hal_i2c.c
  ${HAL_DIR}/Src/stm32f1xx_hal_iwdg.c
  ${HAL_DIR}/Src/stm32f1xx_hal_pwr.c
  ${HAL_DIR}/Src/stm32f1xx_hal_rcc.c
  ${HAL_DIR}/Src/stm32f1xx_hal_rcc_ex.c
  ${HAL_DIR}/Src/stm32f1xx_hal_tim.c
  ${HAL_DIR}/Src/stm32f1xx_hal_tim_ex.c
  ${HAL_DIR}/Src/stm32f1xx_hal_uart.c
)
set(RTOS_SOURCES
  ${FREERTOS_DIR}/tasks.c
  ${FREERTOS_DIR}/queue.c
  ${FREERTOS_DIR}/list.c
  ${FREERTOS_DIR}/timers.c
  ${FREERTOS_DIR}/event_groups.c
  ${FREERTOS_DIR}/stream_buffer.c
  ${FREERTOS_DIR}/portable/GCC/ARM_CM3/port.c
  ${FREERTOS_DIR}/portable/MemMang/heap_4.c
  ${FREERTOS_DIR}/CMSIS_RTOS_V2/cmsis_os2.c
)
file(GLOB APP_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/App/Src/*.c)

add_executable(storemind_node
  Core/Src/main.c
  Core/Src/stm32f1xx_it.c
  Core/Src/stm32f1xx_hal_msp.c
  Core/Src/syscalls.c
  ${CMSIS_DEVICE_DIR}/Source/Templates/system_stm32f1xx.c
  ${CMSIS_DEVICE_DIR}/Source/Templates/gcc/startup_stm32f103xb.s
  ${APP_SOURCES}
  protocol/src/sm_proto.c
  protocol/src/sm_cobs_crc.c
  logic/src/sm_logic.c
  ${HAL_SOURCES}
  ${RTOS_SOURCES}
)
set_target_properties(storemind_node PROPERTIES SUFFIX ".elf")

target_compile_definitions(storemind_node PRIVATE STM32F103xB USE_HAL_DRIVER
  CMSIS_device_header="stm32f1xx.h")          # ST's CMSIS-RTOS2 wrapper includes the device through this
target_include_directories(storemind_node PRIVATE
  Core/Inc
  App/Inc
  protocol/include
  logic/include
  ${CMSIS_CORE_DIR}
  ${CMSIS_RTOS2_DIR}
  ${CMSIS_DEVICE_DIR}/Include
  ${HAL_DIR}/Inc
  ${FREERTOS_DIR}/include
  ${FREERTOS_DIR}/portable/GCC/ARM_CM3
  ${FREERTOS_DIR}/CMSIS_RTOS_V2
)

# Our code is held to -Werror; vendor code is compiled but not warned about.
set(OWN_SOURCES Core/Src/main.c Core/Src/stm32f1xx_it.c Core/Src/stm32f1xx_hal_msp.c Core/Src/syscalls.c ${APP_SOURCES}
    protocol/src/sm_proto.c protocol/src/sm_cobs_crc.c logic/src/sm_logic.c)
set_source_files_properties(${OWN_SOURCES} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror;-Wshadow")
set_source_files_properties(${HAL_SOURCES} ${RTOS_SOURCES}
    ${CMSIS_DEVICE_DIR}/Source/Templates/system_stm32f1xx.c PROPERTIES COMPILE_OPTIONS "-w")

target_link_options(storemind_node PRIVATE
  -T${CMAKE_CURRENT_SOURCE_DIR}/STM32F103C8Tx_FLASH.ld
  -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/storemind_node.map,--cref
  -Wl,--print-memory-usage
  -Wl,--no-warn-rwx-segments
)

add_custom_command(TARGET storemind_node POST_BUILD
  COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:storemind_node> ${CMAKE_CURRENT_BINARY_DIR}/storemind_node.hex
  COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:storemind_node> ${CMAKE_CURRENT_BINARY_DIR}/storemind_node.bin
  COMMAND ${CMAKE_SIZE} --format=berkeley $<TARGET_FILE:storemind_node>
  COMMENT "storemind_node: hex, bin and size")
