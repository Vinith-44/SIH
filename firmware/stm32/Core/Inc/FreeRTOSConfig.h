/*
 * FreeRTOSConfig.h - StoreMind sensor node (STM32F103C8, 72 MHz, 20 KB RAM).
 * The values CMSIS-RTOS2 requires (56 priorities, no port-optimised task
 * selection, 32-bit ticks, ...) are fixed by ST's freertos_os2.h checks.
 */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#if defined(__GNUC__) || defined(__ICCARM__)
#include <stdint.h>
extern uint32_t SystemCoreClock;
#endif

#define configUSE_PREEMPTION                     1
#define configSUPPORT_STATIC_ALLOCATION          0
#define configSUPPORT_DYNAMIC_ALLOCATION         1
#define configUSE_IDLE_HOOK                      0
#define configUSE_TICK_HOOK                      0
#define configCPU_CLOCK_HZ                       (SystemCoreClock)
#define configTICK_RATE_HZ                       ((TickType_t)1000)
#define configMAX_PRIORITIES                     (56)
#define configMINIMAL_STACK_SIZE                 ((uint16_t)96)
#define configTOTAL_HEAP_SIZE                    ((size_t)(9 * 1024))
#define configMAX_TASK_NAME_LEN                  (10)
#define configUSE_TRACE_FACILITY                 1
#define configUSE_16_BIT_TICKS                   0
#define configUSE_MUTEXES                        1
#define configUSE_RECURSIVE_MUTEXES              0
#define configUSE_COUNTING_SEMAPHORES            1
#define configQUEUE_REGISTRY_SIZE                0
#define configUSE_PORT_OPTIMISED_TASK_SELECTION  0
#define configUSE_TASK_NOTIFICATIONS             1
#define configCHECK_FOR_STACK_OVERFLOW           2
#define configUSE_MALLOC_FAILED_HOOK             1
#define configRECORD_STACK_HIGH_ADDRESS          1

/* Software timers (CMSIS-RTOS2 event flags use the timer task). */
#define configUSE_TIMERS                         1
#define configTIMER_TASK_PRIORITY                (2)
#define configTIMER_QUEUE_LENGTH                 4
#define configTIMER_TASK_STACK_DEPTH             (128)

/* API used by CMSIS-RTOS2 and by the health task (stack high-water marks). */
#define INCLUDE_vTaskPrioritySet                 1
#define INCLUDE_uxTaskPriorityGet                1
#define INCLUDE_vTaskDelete                      1
#define INCLUDE_vTaskCleanUpResources            0
#define INCLUDE_vTaskSuspend                     1
#define INCLUDE_xTaskDelayUntil                  1
#define INCLUDE_vTaskDelay                       1
#define INCLUDE_xTaskGetSchedulerState           1
#define INCLUDE_xTimerPendFunctionCall           1
#define INCLUDE_xQueueGetMutexHolder             1
#define INCLUDE_xSemaphoreGetMutexHolder         1
#define INCLUDE_uxTaskGetStackHighWaterMark      1
#define INCLUDE_xTaskGetCurrentTaskHandle        1
#define INCLUDE_eTaskGetState                    1

/* Cortex-M3 has 4 priority bits (0 = highest). */
#define configPRIO_BITS                          4
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY  15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5
#define configKERNEL_INTERRUPT_PRIORITY  (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))

#define configASSERT(x) do { if ((x) == 0) { taskDISABLE_INTERRUPTS(); for (;;) { } } } while (0)

/* Map the FreeRTOS port handlers to the CMSIS names in the vector table.
 * SysTick is ours (Core/Src/stm32f1xx_it.c): it also drives HAL_GetTick(). */
#define vPortSVCHandler    SVC_Handler
#define xPortPendSVHandler PendSV_Handler
#define USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION 1

#endif /* FREERTOS_CONFIG_H */
