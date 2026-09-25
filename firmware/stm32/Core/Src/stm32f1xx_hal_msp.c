/*
 * stm32f1xx_hal_msp.c - pin muxing and clocks for the peripherals HAL owns.
 */
#include "board.h"
#include "stm32f1xx_hal.h"

void HAL_MspInit(void)
{
    __HAL_RCC_AFIO_CLK_ENABLE();
    __HAL_RCC_PWR_CLK_ENABLE();
    /* Keep SWD (PA13/PA14) for flashing, free JTAG-only pins (PA15, PB3, PB4). */
    __HAL_AFIO_REMAP_SWJ_NOJTAG();
    HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);
}

void HAL_UART_MspInit(UART_HandleTypeDef *huart)
{
    GPIO_InitTypeDef g = {0};
    if (huart->Instance == USART1) {
        __HAL_RCC_USART1_CLK_ENABLE();
        g.Pin = PI_TX_PIN;
        g.Mode = GPIO_MODE_AF_PP;
        g.Speed = GPIO_SPEED_FREQ_HIGH;
        HAL_GPIO_Init(PI_TX_PORT, &g);
        g.Pin = PI_RX_PIN;
        g.Mode = GPIO_MODE_INPUT;
        g.Pull = GPIO_PULLUP;                 /* idle-high if the Pi cable is unplugged */
        HAL_GPIO_Init(PI_RX_PORT, &g);
    }
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *hi2c)
{
    GPIO_InitTypeDef g = {0};
    if (hi2c->Instance == I2C1) {
        g.Pin = I2C_SCL_PIN | I2C_SDA_PIN;
        g.Mode = GPIO_MODE_AF_OD;              /* open drain; ONE set of pull-ups on the bus */
        g.Speed = GPIO_SPEED_FREQ_HIGH;
        HAL_GPIO_Init(I2C_SCL_PORT, &g);
        __HAL_RCC_I2C1_CLK_ENABLE();
    }
}

void HAL_I2C_MspDeInit(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance == I2C1) {
        __HAL_RCC_I2C1_CLK_DISABLE();
        HAL_GPIO_DeInit(I2C_SCL_PORT, I2C_SCL_PIN | I2C_SDA_PIN);
    }
}

void HAL_TIM_PWM_MspInit(TIM_HandleTypeDef *htim)
{
    GPIO_InitTypeDef g = {0};
    if (htim->Instance == TIM3) {
        __HAL_RCC_TIM3_CLK_ENABLE();
        g.Pin = SERVO_PIN;
        g.Mode = GPIO_MODE_AF_PP;
        g.Speed = GPIO_SPEED_FREQ_LOW;
        HAL_GPIO_Init(SERVO_PORT, &g);
    }
}
