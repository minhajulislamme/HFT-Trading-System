import logging
import math
import time
from datetime import datetime, timedelta
from modules.config import (
    MAX_OPEN_POSITIONS,
    USE_STOP_LOSS, STOP_LOSS_PCT, 
    TRAILING_STOP, TRAILING_STOP_PCT, UPDATE_TRAILING_ON_HOLD,
    USE_TAKE_PROFIT, USE_DUAL_TAKE_PROFIT,
    TAKE_PROFIT_1_PCT, TAKE_PROFIT_2_PCT, TAKE_PROFIT_1_SIZE_PCT, TAKE_PROFIT_2_SIZE_PCT,
    AUTO_COMPOUND, COMPOUND_REINVEST_PERCENT, COMPOUND_INTERVAL,
    MULTI_INSTANCE_MODE, MAX_POSITIONS_PER_SYMBOL,
    LEVERAGE,
    FIXED_TRADE_PERCENTAGE  # Only variable used for position sizing
)

logger = logging.getLogger(__name__)

# Helper functions
def get_step_size(min_qty_str):
    """Extract step size from min quantity string"""
    step_size = min_qty_str
    if isinstance(step_size, str):
        try:
            step_size = float(step_size)
        except ValueError:
            return 0.001  # Default step size
    
    if step_size == 0:
        return 0.001  # Default step size
    
    return step_size

def round_step_size(quantity, step_size):
    """Round quantity to valid step size"""
    if step_size == 0:
        return quantity
        
    precision = int(round(-math.log10(step_size)))
    if precision < 0:
        precision = 0
    rounded = math.floor(quantity * 10**precision) / 10**precision
    
    # Ensure it's at least the step size
    if rounded < step_size:
        rounded = step_size
        
    return rounded

class RiskManager:
    def __init__(self, binance_client):
        """Initialize risk manager with a reference to binance client"""
        self.binance_client = binance_client
        self.last_compound_time = None
        self.initial_balance = None
        self.last_balance = None
        
    def calculate_position_size(self, symbol, side, price, stop_loss_price=None):
        """
        Calculate position size based on risk parameters
        
        Args:
            symbol: Trading pair symbol
            side: 'BUY' or 'SELL'
            price: Current market price
            stop_loss_price: Optional stop loss price for calculating risk
            
        Returns:
            quantity: The position size
        """
        # Get account balance
        balance = self.binance_client.get_account_balance()
        
        if balance <= 0:
            logger.error("Insufficient balance to open a position")
            return 0
            
        # Get symbol info for precision
        symbol_info = self.binance_client.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"Could not retrieve symbol info for {symbol}")
            return 0
            
        # Use FIXED_TRADE_PERCENTAGE (40%) of account balance for position sizing
        # This is a fixed percentage approach rather than a risk-based approach
        trade_amount = balance * FIXED_TRADE_PERCENTAGE
        logger.debug(f"Using {FIXED_TRADE_PERCENTAGE*100:.1f}% of balance ({balance:.4f} USDT) = {trade_amount:.4f} USDT for trade")
        
        # Calculate position size where trade_amount = margin required (not position value)
        # Position value = margin * leverage, so quantity = (margin * leverage) / price
        # But we want to limit margin to trade_amount, so:
        # quantity = trade_amount * leverage / price
        max_quantity = (trade_amount * LEVERAGE) / price
        
        # The actual margin required will be: (max_quantity * price) / LEVERAGE = trade_amount
        margin_required = (max_quantity * price) / LEVERAGE
        logger.debug(f"Position sizing: margin required = {margin_required:.4f} USDT (should equal trade_amount = {trade_amount:.4f} USDT)")
        
        # Apply precision to quantity
        quantity_precision = symbol_info['quantity_precision']
        step_size = get_step_size(symbol_info['min_qty'])
        
        # Instead of rounding down, find the largest quantity that fits within our budget
        # Start with the maximum possible quantity and round down to step size
        quantity = round_step_size(max_quantity, step_size)
        
        # Check if we can use a larger quantity that still fits within our budget
        # This ensures we maximize the use of our allocated margin
        test_quantity = quantity + step_size
        test_margin = (test_quantity * price) / LEVERAGE
        
        while test_margin <= trade_amount:
            quantity = test_quantity
            test_quantity += step_size
            test_margin = (test_quantity * price) / LEVERAGE
            
        logger.debug(f"Optimized quantity: {quantity} (was {round_step_size(max_quantity, step_size)})")
        
        # Check minimum notional
        min_notional = symbol_info['min_notional']
        if quantity * price < min_notional:
            logger.warning(f"Position size too small - below minimum notional of {min_notional}")
            
            # Calculate minimum quantity needed to meet notional requirement
            min_quantity_for_notional = min_notional / price
            min_quantity = round_step_size(min_quantity_for_notional, step_size)
            
            # If rounded down quantity still doesn't meet notional, round up to next step
            while min_quantity * price < min_notional:
                min_quantity += step_size
            
            # Check if minimum quantity fits within our margin budget
            min_margin_required = (min_quantity * price) / LEVERAGE
            if min_margin_required <= trade_amount:
                quantity = min_quantity
                logger.info(f"Adjusted quantity to {quantity} to meet minimum notional requirement")
                logger.info(f"Margin required for minimum: {min_margin_required:.4f} USDT within budget of {trade_amount:.4f} USDT")
            else:
                logger.warning(f"Cannot meet minimum notional of {min_notional} with {FIXED_TRADE_PERCENTAGE*100:.1f}% trade allocation")
                logger.warning(f"Required margin: {min_margin_required:.4f} USDT, Available: {trade_amount:.4f} USDT")
                return 0
        
        # Final check to ensure we have a valid quantity
        if quantity <= 0:
            logger.error("Balance too low to open even minimum position")
            return 0
        
        # Calculate actual margin that will be used
        actual_margin = (quantity * price) / LEVERAGE
        margin_utilization = (actual_margin / trade_amount) * 100
                
        logger.info(f"Calculated position size: {quantity} units at {price} per unit (using {FIXED_TRADE_PERCENTAGE*100:.1f}% of balance)")
        logger.info(f"Margin required: {actual_margin:.4f} USDT (of {trade_amount:.4f} USDT budget, {margin_utilization:.1f}% utilization), Balance: {balance:.4f} USDT")
        
        return quantity
        
    def should_open_position(self, symbol):
        """Check if a new position should be opened based on risk rules"""
        # Check if we already have an open position for this symbol
        position_info = self.binance_client.get_position_info(symbol)
        if position_info and abs(position_info['position_amount']) > 0:
            logger.info(f"Already have an open position for {symbol}")
            return False
            
        # Check maximum number of open positions
        if MULTI_INSTANCE_MODE:
            # In multi-instance mode, only count positions for the current symbol
            positions = self.binance_client.client.futures_position_information()
            # Check if we've reached the max positions for this symbol
            symbol_positions = [p for p in positions if p['symbol'] == symbol and float(p['positionAmt']) != 0]
            if len(symbol_positions) >= MAX_POSITIONS_PER_SYMBOL:
                logger.info(f"Maximum number of positions for {symbol} ({MAX_POSITIONS_PER_SYMBOL}) reached")
                return False
        else:
            # Original behavior - count all positions
            positions = self.binance_client.client.futures_position_information()
            open_positions = [p for p in positions if float(p['positionAmt']) != 0]
            if len(open_positions) >= MAX_OPEN_POSITIONS:
                logger.info(f"Maximum number of open positions ({MAX_OPEN_POSITIONS}) reached")
                return False
            
        return True
        
    def calculate_stop_loss(self, symbol, side, entry_price):
        """Calculate stop loss price based on configuration"""
        if not USE_STOP_LOSS:
            return None
            
        if side == "BUY":  # Long position
            stop_price = entry_price * (1 - STOP_LOSS_PCT)
        else:  # Short position
            stop_price = entry_price * (1 + STOP_LOSS_PCT)
            
        # Apply price precision
        symbol_info = self.binance_client.get_symbol_info(symbol)
        if symbol_info:
            price_precision = symbol_info['price_precision']
            stop_price = round(stop_price, price_precision)
            
        logger.info(f"Calculated stop loss at {stop_price} ({STOP_LOSS_PCT*100}%)")
        return stop_price
        
    def calculate_dual_take_profit(self, symbol, side, entry_price):
        """
        Calculate dual take profit levels (TP1 and TP2) for a position
        
        Args:
            symbol: Trading pair symbol
            side: Position side ('BUY' for long, 'SELL' for short)
            entry_price: Entry price of the position
            
        Returns:
            dict: Contains tp1_price, tp2_price, tp1_size_pct, tp2_size_pct
        """
        if not USE_TAKE_PROFIT or not USE_DUAL_TAKE_PROFIT:
            return None
            
        # Get symbol info for price precision
        symbol_info = self.binance_client.get_symbol_info(symbol)
        price_precision = symbol_info['price_precision'] if symbol_info else 6
        
        if side == "BUY":  # Long position
            tp1_price = entry_price * (1 + TAKE_PROFIT_1_PCT)
            tp2_price = entry_price * (1 + TAKE_PROFIT_2_PCT)
        else:  # Short position
            tp1_price = entry_price * (1 - TAKE_PROFIT_1_PCT)
            tp2_price = entry_price * (1 - TAKE_PROFIT_2_PCT)
            
        # Round to symbol precision
        tp1_price = round(tp1_price, price_precision)
        tp2_price = round(tp2_price, price_precision)
        
        dual_tp = {
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'tp1_size_pct': TAKE_PROFIT_1_SIZE_PCT,
            'tp2_size_pct': TAKE_PROFIT_2_SIZE_PCT
        }
        
        logger.info(f"Calculated dual take profit for {side} position at {entry_price}:")
        logger.info(f"  TP1: {tp1_price} ({TAKE_PROFIT_1_PCT*100:.1f}%) - {TAKE_PROFIT_1_SIZE_PCT*100:.0f}% position")
        logger.info(f"  TP2: {tp2_price} ({TAKE_PROFIT_2_PCT*100:.1f}%) - {TAKE_PROFIT_2_SIZE_PCT*100:.0f}% position")
        
        return dual_tp
    
    def _get_current_stop_loss_price(self, symbol, side, entry_price):
        """
        Get the actual current stop loss price from existing orders.
        If no stop loss order exists, calculate it from entry price.
        
        Args:
            symbol: Trading pair symbol
            side: Position side ('BUY' or 'SELL')
            entry_price: Entry price of the position
            
        Returns:
            float: Current stop loss price
        """
        try:
            # Get existing stop loss orders for this symbol
            orders = self.binance_client.get_open_orders(symbol)
            
            for order in orders:
                # Look for stop loss orders (STOP_MARKET or STOP)
                if order.get('type') in ['STOP_MARKET', 'STOP'] and order.get('symbol') == symbol:
                    stop_price = float(order.get('stopPrice', 0))
                    if stop_price > 0:
                        logger.debug(f"Found existing stop loss order at {stop_price} for {symbol}")
                        return stop_price
                        
        except Exception as e:
            logger.warning(f"Error getting current stop loss price from orders: {e}")
            
        # If no existing stop loss order found, calculate from entry price
        logger.debug(f"No existing stop loss order found, calculating from entry price {entry_price}")
        return self.calculate_stop_loss(symbol, side, entry_price)
        
    def adjust_stop_loss_for_trailing(self, symbol, side, current_price, position_info=None):
        """
        Adjust stop loss for trailing stop if needed - ONLY moves in favor of the trader
        
        This function ensures:
        1. Stop loss NEVER moves against the trader (increases risk)
        2. Stop loss only moves to lock in profits or reduce losses
        3. For LONG positions: stop loss can only move UP (higher price)
        4. For SHORT positions: stop loss can only move DOWN (lower price)
        """
        if not TRAILING_STOP:
            return None
            
        if not position_info:
            # Get position info specifically for this symbol
            position_info = self.binance_client.get_position_info(symbol)
            
        # Only proceed if we have a valid position for this specific symbol
        if not position_info or abs(position_info['position_amount']) == 0:
            return None
            
        # Ensure we're dealing with the right symbol
        if position_info['symbol'] != symbol:
            logger.warning(f"Position symbol mismatch: expected {symbol}, got {position_info['symbol']}")
            return None
            
        entry_price = position_info['entry_price']
        
        # Get current stop loss to compare - use ACTUAL stop loss from existing orders, not calculated from entry
        current_stop = self._get_current_stop_loss_price(symbol, side, entry_price)
        
        # Calculate new trailing stop loss based on current price
        if side == "BUY":  # Long position
            new_stop = current_price * (1 - TRAILING_STOP_PCT)
            
            # FOR LONG POSITIONS: Stop loss can ONLY move UP (never down)
            # This protects profits and never increases risk
            if current_stop and new_stop <= current_stop:
                logger.debug(f"Trailing stop NOT moved: new stop ({new_stop:.6f}) would be same or lower than current ({current_stop:.6f})")
                logger.debug(f"Long position: stop loss only moves UP to protect profits")
                return None
                
            # Additional check: ensure we're actually in profit territory
            if new_stop <= entry_price:
                logger.debug(f"Trailing stop not at profit level yet - current: {new_stop:.6f}, entry: {entry_price:.6f}")
                
        else:  # Short position
            new_stop = current_price * (1 + TRAILING_STOP_PCT)
            
            # FOR SHORT POSITIONS: Stop loss can ONLY move DOWN (never up)  
            # This protects profits and never increases risk
            if current_stop and new_stop >= current_stop:
                logger.debug(f"Trailing stop NOT moved: new stop ({new_stop:.6f}) would be same or higher than current ({current_stop:.6f})")
                logger.debug(f"Short position: stop loss only moves DOWN to protect profits")
                return None
                
            # Additional check: ensure we're actually in profit territory
            if new_stop >= entry_price:
                logger.debug(f"Trailing stop not at profit level yet - current: {new_stop:.6f}, entry: {entry_price:.6f}")
                
        # Apply price precision
        symbol_info = self.binance_client.get_symbol_info(symbol)
        if symbol_info:
            price_precision = symbol_info['price_precision']
            new_stop = round(new_stop, price_precision)
            
        # Calculate profit protection
        if side == "BUY":
            profit_locked = ((new_stop - entry_price) / entry_price) * 100
        else:
            profit_locked = ((entry_price - new_stop) / entry_price) * 100
            
        logger.info(f"✅ TRAILING STOP MOVED IN FAVORABLE DIRECTION ✅")
        logger.info(f"Symbol: {symbol} | Side: {side}")
        logger.info(f"Entry: {entry_price:.6f} | Current: {current_price:.6f}")
        logger.info(f"Stop Loss: {current_stop:.6f} → {new_stop:.6f}")
        logger.info(f"Profit protected: {profit_locked:.2f}%")
        
        return new_stop
    
    def update_balance_for_compounding(self):
        """Update balance tracking for auto-compounding"""
        if not AUTO_COMPOUND:
            return False
            
        # Get current account balance
        current_balance = self.binance_client.get_account_balance()
        
        # Initialize balance tracking if needed
        if self.initial_balance is None:
            self.initial_balance = current_balance
            self.last_balance = current_balance
            self.last_compound_time = datetime.now()
            logger.info(f"Initialized compounding with balance: {current_balance}")
            return False
            
        # Check if it's time to compound based on the configured interval
        now = datetime.now()
        compound_interval_days = 1  # Default to daily
        
        if COMPOUND_INTERVAL == 'HOURLY':
            compound_interval_days = 1/24
        elif COMPOUND_INTERVAL == 'DAILY':
            compound_interval_days = 1
        elif COMPOUND_INTERVAL == 'WEEKLY':
            compound_interval_days = 7
        elif COMPOUND_INTERVAL == 'MONTHLY':
            compound_interval_days = 30
            
        time_since_last_compound = now - self.last_compound_time
        
        # Check if it's time to compound
        if time_since_last_compound.total_seconds() < compound_interval_days * 24 * 3600:
            return False
            
        # Calculate profit
        profit = current_balance - self.last_balance
        
        if profit <= 0:
            logger.info(f"No profit to compound. Current balance: {current_balance}, Previous: {self.last_balance}")
            self.last_compound_time = now
            self.last_balance = current_balance
            return False
            
        # Apply compounding by updating risk amount
        # This effectively increases position sizes based on profits
        compound_amount = profit * COMPOUND_REINVEST_PERCENT
        logger.info(f"Compounding {COMPOUND_REINVEST_PERCENT*100}% of profit: {profit} = {compound_amount}")
        
        # Update last compound time and balance
        self.last_compound_time = now
        self.last_balance = current_balance
        
        return True
    


    def test_position_sizing(self, symbol='BTCUSDT'):
        """
        Test method to verify position sizing using FIXED_TRADE_PERCENTAGE approach
        
        Args:
            symbol: Trading symbol to test with
        
        Returns:
            dict: Information about current position sizing settings
        """
        current_price = self.binance_client.get_symbol_price(symbol)
        balance = self.binance_client.get_account_balance()
        
        # Calculate trade amount using FIXED_TRADE_PERCENTAGE (40%)
        trade_amount = balance * FIXED_TRADE_PERCENTAGE
        
        # Calculate position size: margin = trade_amount, so position_size = (margin * leverage) / price
        position_size = (trade_amount * LEVERAGE) / current_price
        
        # Calculate actual margin required (should equal trade_amount)
        margin_required = (position_size * current_price) / LEVERAGE
        
        return {
            'symbol': symbol,
            'current_price': current_price,
            'account_balance': balance,
            'fixed_trade_percentage': FIXED_TRADE_PERCENTAGE,
            'trade_amount_budget': trade_amount,
            'position_size': position_size,
            'leverage': LEVERAGE,
            'margin_required': margin_required,
            'margin_as_pct_of_balance': (margin_required / balance) * 100,
            'position_sizing_method': 'FIXED_TRADE_PERCENTAGE - Margin limited to trade amount'
        }

    def check_margin_sufficient(self, symbol, price, quantity):
        """
        Check if there's sufficient margin available for the requested position size
        Uses FIXED_TRADE_PERCENTAGE approach - no margin safety overrides
        
        Args:
            symbol: Trading pair symbol
            price: Current market price
            quantity: Position size to check
            
        Returns:
            bool: True if there's sufficient margin, False otherwise
        """
        # Get account balance
        balance = self.binance_client.get_account_balance()
        
        # Calculate required margin for the position
        required_margin = (quantity * price) / LEVERAGE
        
        # Check if required margin fits within the fixed trade percentage allocation
        max_trade_amount = balance * FIXED_TRADE_PERCENTAGE
        
        if required_margin > max_trade_amount:
            logger.warning(f"Position exceeds fixed trade allocation:")
            logger.warning(f"  Required margin: {required_margin:.4f} USDT")
            logger.warning(f"  Max trade amount: {max_trade_amount:.4f} USDT ({FIXED_TRADE_PERCENTAGE*100:.1f}% of {balance:.4f} USDT balance)")
            return False
        
        logger.debug(f"Margin check passed: Required {required_margin:.4f} USDT within {FIXED_TRADE_PERCENTAGE*100:.1f}% allocation ({max_trade_amount:.4f} USDT)")
        return True

    def clear_locked_trailing_stop(self, symbol):
        """
        Clear any locked trailing stop state for a symbol when position is closed.
        This method is called when a position is closed to reset any trailing stop
        tracking state that might interfere with new positions.
        
        Args:
            symbol: Trading pair symbol
        """
        # This method is a placeholder for future trailing stop state management
        # Currently, trailing stops are managed through Binance orders directly
        # and don't require additional state clearing
        logger.debug(f"Cleared locked trailing stop state for {symbol}")
        return True