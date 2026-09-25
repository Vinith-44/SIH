/*
 * stm32f1xx_it.c - interrupt handlers.
 * SVC and PendSV are FreeRTOS's (FreeRTOSConfig.h maps them); SysTick is here
 * because it drives both HAL_GetTick() and the FreeRTOS tick.
 */
#include "FreeRTOS.h"
#include "task.h"

#include "app.h"
#include "board.h"
#include "drivers.h"

extern void xPortSysTickHandler(void);

void NMI_Handler(void)
{
    for (;;) {
    }
}

void HardFault_Handler(void)
{
    for (;;) {             /* IWDG resets the node; $H then reports reset_cause=IWDG */
    }
}

void MemManage_Handler(void)
{
    for (;;) {
    }
}

void BusFault_Handler(void)
{
    for (;;) {
    }
}

void UsageFault_Handler(void)
{
    for (;;) {
    }
}

void DebugMon_Handler(void)
{
}

void SysTick_Handler(void)
{
    HAL_IncTick();
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        xPortSysTickHandler();
    }
}

/* USART1: receive byte by byte into the ring buffer; count overruns. */
void USART1_IRQHandler(void)
{
    uint32_t sr = PI_UART->SR;
    if (sr & (USART_SR_ORE | USART_SR_FE | USART_SR_NE)) {
        g_uart_err++;
        (void)PI_UART->DR;                     /* reading SR then DR clears the error */
        return;
    }
    if (sr & USART_SR_RXNE) {
        app_uart_rx_isr((uint8_t)(PI_UART->DR & 0xFFu));
    }
}

void EXTI0_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(HX711_1_DOUT_PIN);
}

void EXTI4_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(HX711_2_DOUT_PIN);
}

void EXTI9_5_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(MEMS_INT_PIN);
}

void EXTI15_10_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(BEAM_OUTER_PIN);
    HAL_GPIO_EXTI_IRQHandler(BEAM_INNER_PIN);
    HAL_GPIO_EXTI_IRQHandler(PIR_PIN);
    HAL_GPIO_EXTI_IRQHandler(RESTOCK_PIN);
}
