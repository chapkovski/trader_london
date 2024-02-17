from pydantic import BaseModel, Field

class TraderCreationData(BaseModel):
    max_short_shares: int = Field(default=100, description="Maximum number of shares for shorting")
    max_short_cash: float = Field(default=10000.0, description="Maximum amount of cash for shorting")
    initial_cash: float = Field(default=1000.0, description="Initial amount of cash")
    initial_shares: int = Field(default=0, description="Initial amount of shares")
    trading_day_duration: int = Field(default=5, description="Duration of the trading day in minutes")
    max_active_orders: int = Field(default=5, description="Maximum amount of active orders")
    noise_trader_update_freq: int = Field(default=10, description="Frequency of noise traders' updates in seconds")
    step: int = Field(default=100, description="Step for new orders")

    class Config:
        schema_extra = {
            "example": {
                "max_short_shares": 100,
                "max_short_cash": 10000.0,
                "initial_cash": 1000.0,
                "initial_shares": 0,
                "trading_day_duration": 5,  # Representing 8 hours in minutes
                "max_active_orders": 5,
                "noise_trader_update_freq": 10,  # in seconds,
                "step": 100
            }
        }
