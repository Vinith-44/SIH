/*
 * main.c - StoreMind sensor node (STM32F103C8 "Blue Pill" + FreeRTOS / CMSIS-RTOS2).
 *
 * Laid out like an STM32CubeMX project (MX_*_Init functions, Core/ + App/), but
 * written by hand so it builds from a clean clone with CMake only; the pin map
 * is in board.h and docs/WIRING.md.  Everything after the scheduler starts lives
 * in App/ (the 8 tasks).
 */
#include "app.h"
#include "board.h"
#include "drivers.h"
#include "node_config.h"

I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart1;
TIM_HandleTypeDef htim3;
IWDG_HandleTypeDef hiwdg;

static void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_I2C1_Init(void);
static void MX_TIM3_Init(void);
static void MX_IWDG_Init(void);
void Error_Handler(void);

/* Why did we (re)start?  Read before anything clears the flags. */
static sm_reset_t read_reset_cause(void)
{
    uint32_t csr = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF;
    if (csr & RCC_CSR_LPWRRSTF) {
        return SM_RESET_LPWR;
    }
    if (csr & RCC_CSR_WWDGRSTF) {
        return SM_RESET_WWDG;
    }
    if (csr & RCC_CSR_IWDGRSTF) {
        return SM_RESET_IWDG;
    }
    if (csr & RCC_CSR_SFTRSTF) {
        return SM_RESET_SW;
    }
    if (csr & RCC_CSR_PORRSTF) {
        return SM_RESET_POR;       /* power-on also sets PINRSTF, so test it first */
    }
    if (csr & RCC_CSR_PINRSTF) {
        return SM_RESET_PIN;
    }
    return SM_RESET_UNKNOWN;
}

int main(void)
{
    g_reset_cause = read_reset_cause();
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_USART1_UART_Init();
    MX_I2C1_Init();
    MX_TIM3_Init();
    node_config_load();
    MX_IWDG_Init();

    osKernelInitialize();
    app_start();
    osKernelStart();

    for (;;) {             /* never reached */
    }
}

/* 8 MHz HSE x 9 = 72 MHz SYSCLK; APB1 36 MHz, APB2 72 MHz. */
static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE | RCC_OSCILLATORTYPE_LSI;
    osc.HSEState = RCC_HSE_ON;
    osc.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
    osc.LSIState = RCC_LSI_ON;                 /* for the IWDG */
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLMUL = RCC_PLL_MUL9;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        Error_Handler();
    }
    clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV2;
    clk.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_2) != HAL_OK) {
        Error_Handler();
    }
}

static void gpio_out(GPIO_TypeDef *port, uint16_t pin, GPIO_PinState initial)
{
    GPIO_InitTypeDef g = {0};
    HAL_GPIO_WritePin(port, pin, initial);
    g.Pin = pin;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(port, &g);
}

static void gpio_exti(GPIO_TypeDef *port, uint16_t pin, uint32_t mode, uint32_t pull)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = pin;
    g.Mode = mode;
    g.Pull = pull;
    HAL_GPIO_Init(port, &g);
}

static void MX_GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();              /* HSE pins */
    __HAL_RCC_AFIO_CLK_ENABLE();

    gpio_out(HX711_1_SCK_PORT, HX711_1_SCK_PIN, GPIO_PIN_RESET);
    gpio_out(HX711_2_SCK_PORT, HX711_2_SCK_PIN, GPIO_PIN_RESET);
    gpio_out(LED_ALERT_PORT, LED_ALERT_PIN, GPIO_PIN_RESET);
    gpio_out(BUZZER_PORT, BUZZER_PIN, GPIO_PIN_RESET);
    gpio_out(LED_STATUS_PORT, LED_STATUS_PIN, GPIO_PIN_SET);        /* active low: off */

    gpio_exti(HX711_1_DOUT_PORT, HX711_1_DOUT_PIN, GPIO_MODE_IT_FALLING, GPIO_PULLUP);
    gpio_exti(HX711_2_DOUT_PORT, HX711_2_DOUT_PIN, GPIO_MODE_IT_FALLING, GPIO_PULLUP);
    gpio_exti(BEAM_OUTER_PORT, BEAM_OUTER_PIN, GPIO_MODE_IT_RISING_FALLING, GPIO_PULLUP);
    gpio_exti(BEAM_INNER_PORT, BEAM_INNER_PIN, GPIO_MODE_IT_RISING_FALLING, GPIO_PULLUP);
    gpio_exti(PIR_PORT, PIR_PIN, GPIO_MODE_IT_RISING_FALLING, GPIO_PULLDOWN);
    gpio_exti(RESTOCK_PORT, RESTOCK_PIN, GPIO_MODE_IT_RISING_FALLING, GPIO_PULLUP);
    gpio_exti(MEMS_INT_PORT, MEMS_INT_PIN, GPIO_MODE_IT_RISING, GPIO_NOPULL);

    /* Priority 6: below configMAX_SYSCALL_INTERRUPT_PRIORITY (5), so the
     * handlers may use the FreeRTOS ...FromISR API. */
    const IRQn_Type irqs[] = {EXTI0_IRQn, EXTI4_IRQn, EXTI9_5_IRQn, EXTI15_10_IRQn};
    for (unsigned i = 0; i < sizeof irqs / sizeof irqs[0]; i++) {
        HAL_NVIC_SetPriority(irqs[i], 6, 0);
        HAL_NVIC_EnableIRQ(irqs[i]);
    }
}

static void MX_USART1_UART_Init(void)
{
    huart1.Instance = PI_UART;
    huart1.Init.BaudRate = PI_UART_BAUD;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    if (HAL_UART_Init(&huart1) != HAL_OK) {
        Error_Handler();
    }
    HAL_NVIC_SetPriority(USART1_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
}

static void MX_I2C1_Init(void)
{
    hi2c1.Instance = I2C1;
    hi2c1.Init.ClockSpeed = I2C_SPEED_HZ;
    hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c1.Init.OwnAddress1 = 0;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK) {
        Error_Handler();
    }
    i2c_bus_init();
}

/* Servo: TIM3 CH1 (PA6), 1 MHz tick, 20 ms period. */
static void MX_TIM3_Init(void)
{
    TIM_OC_InitTypeDef oc = {0};
    htim3.Instance = TIM3;
    htim3.Init.Prescaler = 72u - 1u;          /* TIM3 clock is 72 MHz (APB1 x2) */
    htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim3.Init.Period = 20000u - 1u;
    htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    if (HAL_TIM_PWM_Init(&htim3) != HAL_OK) {
        Error_Handler();
    }
    oc.OCMode = TIM_OCMODE_PWM1;
    oc.Pulse = 1500u;                          /* centre */
    oc.OCPolarity = TIM_OCPOLARITY_HIGH;
    oc.OCFastMode = TIM_OCFAST_DISABLE;
    if (HAL_TIM_PWM_ConfigChannel(&htim3, &oc, TIM_CHANNEL_1) != HAL_OK) {
        Error_Handler();
    }
}

/* IWDG: LSI ~40 kHz / 64 = 625 Hz; reload 2500 -> ~4 s (SM_WATCHDOG_MS). */
static void MX_IWDG_Init(void)
{
    hiwdg.Instance = IWDG;
    hiwdg.Init.Prescaler = IWDG_PRESCALER_64;
    hiwdg.Init.Reload = (SM_WATCHDOG_MS * 625u) / 1000u;
    if (HAL_IWDG_Init(&hiwdg) != HAL_OK) {
        Error_Handler();
    }
}

void Error_Handler(void)
{
    __disable_irq();
    for (;;) {             /* the IWDG (if running) resets us */
    }
}
