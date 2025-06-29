from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import pandas as pd
import logging
import warnings
import traceback
import math
from collections import deque

# Setup logging
logger = logging.getLogger(__name__)
warnings.simplefilter(action='ignore', category=FutureWarning)

# Import price action configuration values
try:
    from modules.config import (
        PRICE_ACTION_LOOKBACK,
        BREAKOUT_THRESHOLD,
        VOLATILITY_WINDOW,
        MOMENTUM_WINDOW,
        SUPPORT_RESISTANCE_STRENGTH
    )
except ImportError:
    # Fallback values for pure price action strategies
    PRICE_ACTION_LOOKBACK = 20
    BREAKOUT_THRESHOLD = 0.02  # 2% breakout threshold
    VOLATILITY_WINDOW = 14
    MOMENTUM_WINDOW = 10
    SUPPORT_RESISTANCE_STRENGTH = 3


class TradingStrategy:
    """Base trading strategy class for pure price action strategies"""
    
    def __init__(self, name="BaseStrategy"):
        self.name = name
        self.risk_manager = None
        self.last_signal = None
        self.signal_history = deque(maxlen=100)  # Keep last 100 signals for analysis
    
    @property
    def strategy_name(self):
        """Property to access strategy name (for compatibility)"""
        return self.name
        
    def set_risk_manager(self, risk_manager):
        """Set the risk manager for this strategy"""
        self.risk_manager = risk_manager
        
    def get_signal(self, klines):
        """Get trading signal from klines data. Override in subclasses."""
        return None
        
    def add_indicators(self, df):
        """Add mathematical price action calculations to dataframe. Override in subclasses."""
        return df
    
    def calculate_price_momentum(self, prices, window=10):
        """Calculate pure price momentum without indicators"""
        if len(prices) < window + 1:
            return 0
        
        current_price = prices[-1]
        past_price = prices[-window-1]
        
        # Prevent division by zero
        if past_price == 0 or past_price is None:
            return 0
        
        momentum = (current_price - past_price) / past_price
        return momentum
    
    def calculate_volatility(self, prices, window=14):
        """Calculate price volatility using standard deviation"""
        if len(prices) < window:
            return 0
        
        recent_prices = prices[-window:]
        returns = []
        
        for i in range(1, len(recent_prices)):
            prev_price = recent_prices[i-1]
            curr_price = recent_prices[i]
            
            # Prevent division by zero
            if prev_price == 0 or prev_price is None:
                continue
                
            return_val = (curr_price - prev_price) / prev_price
            returns.append(return_val)
        
        if not returns:
            return 0
        
        volatility = np.std(returns)
        return volatility
    
    def find_support_resistance(self, highs, lows, strength=3):
        """Find support and resistance levels using price action"""
        if len(highs) < strength * 2 + 1 or len(lows) < strength * 2 + 1:
            return [], []
        
        resistance_levels = []
        support_levels = []
        
        # Find resistance levels (local highs)
        for i in range(strength, len(highs) - strength):
            is_resistance = True
            for j in range(i - strength, i + strength + 1):
                if j != i and highs[j] >= highs[i]:
                    is_resistance = False
                    break
            if is_resistance:
                resistance_levels.append(highs[i])
        
        # Find support levels (local lows)
        for i in range(strength, len(lows) - strength):
            is_support = True
            for j in range(i - strength, i + strength + 1):
                if j != i and lows[j] <= lows[i]:
                    is_support = False
                    break
            if is_support:
                support_levels.append(lows[i])
        
        return resistance_levels, support_levels
    
    def detect_candlestick_patterns(self, ohlc_data):
        """Detect basic candlestick patterns using pure price action"""
        if len(ohlc_data) < 2:
            return None
        
        try:
            current = ohlc_data[-1]
            prev = ohlc_data[-2] if len(ohlc_data) >= 2 else current
            
            # Validate OHLC data
            required_keys = ['open', 'high', 'low', 'close']
            for key in required_keys:
                if key not in current or current[key] is None or current[key] <= 0:
                    return None
                if key not in prev or prev[key] is None or prev[key] <= 0:
                    return None
            
            o, h, l, c = current['open'], current['high'], current['low'], current['close']
            prev_o, prev_h, prev_l, prev_c = prev['open'], prev['high'], prev['low'], prev['close']
            
            # Validate OHLC relationships
            if not (l <= min(o, c) <= max(o, c) <= h):
                return None
            if not (prev_l <= min(prev_o, prev_c) <= max(prev_o, prev_c) <= prev_h):
                return None
            
            body_size = abs(c - o)
            prev_body_size = abs(prev_c - prev_o)
            total_range = h - l
            
            # Avoid patterns on very small ranges
            if total_range == 0:
                return None
            
            # Bullish patterns
            if c > o:  # Green candle
                # Hammer pattern (need meaningful lower shadow)
                lower_shadow = min(o, c) - l
                upper_shadow = h - max(o, c)
                
                if (body_size > 0 and lower_shadow > 2 * body_size and 
                    upper_shadow < body_size * 0.2):
                    return "BULLISH_HAMMER"
                
                # Bullish engulfing (need meaningful previous body)
                if (prev_body_size > 0 and prev_c < prev_o and 
                    c > prev_o and o < prev_c and body_size > prev_body_size):
                    return "BULLISH_ENGULFING"
            
            # Bearish patterns
            elif c < o:  # Red candle
                # Hanging man pattern
                lower_shadow = min(o, c) - l
                upper_shadow = h - max(o, c)
                
                if (body_size > 0 and lower_shadow > 2 * body_size and 
                    upper_shadow < body_size * 0.2):
                    return "BEARISH_HANGING_MAN"
                
                # Bearish engulfing
                if (prev_body_size > 0 and prev_c > prev_o and 
                    c < prev_o and o > prev_c and body_size > prev_body_size):
                    return "BEARISH_ENGULFING"
            
            # Doji pattern (very small body relative to range)
            if total_range > 0 and body_size < total_range * 0.1:
                return "DOJI"
            
            return None
            
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Error in candlestick pattern detection: {e}")
            return None


class PurePriceActionStrategy(TradingStrategy):
    """
    Enhanced Zone-Based Pure Price Action Strategy:
    
    ==========================================
    TRADING METHODOLOGY: ZONE FIRST, CONFIRMATION SECOND
    ==========================================
    
    STEP 1: IDENTIFY HIGH-QUALITY SUPPORT/RESISTANCE ZONES
    - Uses historical price data to identify strong S/R levels
    - Creates ZONES (not just lines) with upper/lower bounds
    - Calculates zone strength based on:
      * Number of touches/tests
      * Time spent in zone
      * Number of rejections
      * Volume confirmation
    - Only trades zones with strength ≥ 3 (minimum quality threshold)
    
    STEP 2: WAIT FOR PRICE TO ENTER STRONG ZONES
    - Monitors price approaching high-strength zones
    - Distinguishes between support zones and resistance zones
    - Tracks whether price is within zone boundaries
    
    STEP 3: LOOK FOR PRICE ACTION CONFIRMATION PATTERNS
    - Pin Bars (Hammer/Shooting Star): Strong rejection candles
    - Engulfing Patterns: Momentum continuation signals
    - Zone Rejections: Bounces from S/R with strong close
    - Breakout Confirmations: Clean breaks with volume
    - Double Top/Bottom: Multiple tests with confirmation
    
    STEP 4: GENERATE SIGNALS ONLY WHEN BOTH CONDITIONS MET
    - Requires BOTH zone identification AND price action confirmation
    - Uses scoring system (minimum 4/10 strength required)
    - Provides detailed signal reasoning and strength
    - No signals in weak zones or without clear patterns
    
    ==========================================
    SIGNAL TYPES & CONDITIONS
    ==========================================
    
    🟢 BUY SIGNALS:
    1. SUPPORT ZONE REJECTION (Highest Priority)
       - Price enters strong support zone
       - Shows bullish rejection pattern (pin bar, engulfing, etc.)
       - Closes above zone with strength
    
    2. RESISTANCE BREAKOUT
       - Price breaks above strong resistance zone
       - Confirmed with volume and strong candle
       - Momentum continuation above breakout level
    
    3. MOMENTUM + ZONE CONFIRMATION
       - Strong positive momentum near support
       - Continuation after support bounce
    
    🔴 SELL SIGNALS:
    1. RESISTANCE ZONE REJECTION (Highest Priority)
       - Price enters strong resistance zone
       - Shows bearish rejection pattern (shooting star, engulfing, etc.)
       - Closes below zone with strength
    
    2. SUPPORT BREAKDOWN
       - Price breaks below strong support zone
       - Confirmed with volume and strong candle
       - Momentum continuation below breakdown level
    
    3. MOMENTUM + ZONE CONFIRMATION
       - Strong negative momentum near resistance
       - Continuation after resistance rejection
    
    ⚪ HOLD SIGNALS:
    - Price not in any strong zones
    - Weak zones (strength < 3)
    - No clear price action confirmation
    - Conflicting signals
    - Insufficient signal strength (< 4/10)
    
    ==========================================
    BENEFITS OF THIS APPROACH
    ==========================================
    
    ✅ Higher Probability Trades:
    - Only trades at proven S/R levels with multiple confirmations
    - Reduces false signals by requiring zone + pattern confirmation
    
    ✅ Better Risk Management:
    - Clear entry points at logical S/R levels
    - Natural stop-loss placement (beyond zones)
    - Defined reward-to-risk ratios
    
    ✅ Market Structure Focus:
    - Trades with market structure, not against it
    - Recognizes market memory at key levels
    - Adapts to changing market conditions
    
    ✅ Reduced Noise:
    - Filters out weak signals in no-man's land
    - Focuses only on high-conviction setups
    - Provides clear reasoning for each signal
    
    Mathematical Components:
    - Zone strength calculation using historical interactions
    - Price action pattern recognition algorithms
    - Momentum and volatility analysis
    - Volume confirmation when available
    - Multi-timeframe support/resistance analysis
    """
    
    def __init__(self, 
                 lookback_period=20,        # Lookback period for analysis
                 breakout_threshold=0.02,   # 2% breakout threshold
                 volatility_window=14,      # Volatility calculation window
                 momentum_window=10,        # Momentum calculation window
                 sr_strength=3):            # Support/resistance strength
        
        super().__init__("PurePriceActionStrategy")
        
        # Parameter validation
        if lookback_period <= 0:
            raise ValueError("Lookback period must be positive")
        if breakout_threshold <= 0:
            raise ValueError("Breakout threshold must be positive") 
        if volatility_window <= 0:
            raise ValueError("Volatility window must be positive")
        if momentum_window <= 0:
            raise ValueError("Momentum window must be positive")
        if sr_strength <= 0:
            raise ValueError("Support/resistance strength must be positive")
        
        # Store parameters
        self.lookback_period = lookback_period
        self.breakout_threshold = breakout_threshold
        self.volatility_window = volatility_window
        self.momentum_window = momentum_window
        self.sr_strength = sr_strength
        self._warning_count = 0
        
        logger.info(f"{self.name} initialized with:")
        logger.info(f"  Lookback Period: {lookback_period} candles")
        logger.info(f"  Breakout Threshold: {breakout_threshold*100}%")
        logger.info(f"  Volatility Window: {volatility_window} periods")
        logger.info(f"  Momentum Window: {momentum_window} periods")
        logger.info(f"  Support/Resistance Strength: {sr_strength}")
    
    def add_indicators(self, df):
        """Add pure price action calculations"""
        try:
            # Ensure sufficient data
            min_required = max(self.lookback_period, self.volatility_window, self.momentum_window) + 5
            if len(df) < min_required:
                logger.warning(f"Insufficient data: need {min_required}, got {len(df)}")
                return df
            
            # Data cleaning
            if df['close'].isna().any():
                logger.warning("Found NaN values in close prices, cleaning data")
                df['close'] = df['close'].interpolate(method='linear').bfill().ffill()
                
            # Ensure positive prices
            price_cols = ['open', 'high', 'low', 'close']
            for col in price_cols:
                if (df[col] <= 0).any():
                    logger.warning(f"Found zero or negative values in {col}, using interpolation")
                    df[col] = df[col].replace(0, np.nan)
                    df[col] = df[col].interpolate(method='linear').bfill().ffill()
            
            # Calculate price momentum
            df['price_momentum'] = df['close'].pct_change(periods=self.momentum_window)
            
            # Calculate volatility using rolling standard deviation
            df['returns'] = df['close'].pct_change()
            df['volatility'] = df['returns'].rolling(window=self.volatility_window).std()
            
            # Calculate price range
            df['price_range'] = df['high'] - df['low']
            df['avg_range'] = df['price_range'].rolling(window=self.lookback_period).mean()
            
            # Calculate support and resistance levels
            df['resistance'] = df['high'].rolling(window=self.lookback_period).max()
            df['support'] = df['low'].rolling(window=self.lookback_period).min()
            
            # Distance from support/resistance (with division by zero protection)
            df['dist_from_resistance'] = np.where(
                df['close'] != 0, 
                (df['resistance'] - df['close']) / df['close'], 
                0
            )
            df['dist_from_support'] = np.where(
                df['close'] != 0,
                (df['close'] - df['support']) / df['close'],
                0
            )
            
            # Breakout detection
            df['near_resistance'] = df['dist_from_resistance'] < self.breakout_threshold
            df['near_support'] = df['dist_from_support'] < self.breakout_threshold
            
            # Price position within range (with division by zero protection)
            range_size = df['resistance'] - df['support']
            df['price_position'] = np.where(
                range_size != 0,
                (df['close'] - df['support']) / range_size,
                0.5  # Default to middle position if no range
            )
            
            # Volume-price analysis (if volume available)
            if 'volume' in df.columns:
                df['avg_volume'] = df['volume'].rolling(window=self.lookback_period).mean()
                # Protect against division by zero for volume ratio
                df['volume_ratio'] = np.where(
                    df['avg_volume'] != 0,
                    df['volume'] / df['avg_volume'],
                    1.0  # Default to 1.0 if no average volume
                )
                df['price_volume_momentum'] = df['price_momentum'] * df['volume_ratio']
            
            # Candlestick body analysis
            df['body_size'] = abs(df['close'] - df['open'])
            df['upper_shadow'] = df['high'] - np.maximum(df['open'], df['close'])
            df['lower_shadow'] = np.minimum(df['open'], df['close']) - df['low']
            df['total_range'] = df['high'] - df['low']
            
            # Relative body size (with division by zero protection)
            df['body_ratio'] = np.where(
                df['total_range'] != 0,
                df['body_size'] / df['total_range'],
                0.5  # Default to 0.5 if no range
            )
            
            # Enhanced Support/Resistance Zone Analysis
            
            # Calculate more precise support/resistance levels using pivots
            df['pivot_high'] = df['high'].rolling(window=5, center=True).max() == df['high']
            df['pivot_low'] = df['low'].rolling(window=5, center=True).min() == df['low']
            
            # Dynamic support/resistance based on recent price action
            df['dynamic_resistance'] = df['high'].rolling(window=self.lookback_period//2).max()
            df['dynamic_support'] = df['low'].rolling(window=self.lookback_period//2).min()
            
            # Create Support/Resistance ZONES (not just lines)
            zone_width = 0.005  # 0.5% zone width
            
            # Resistance Zone (upper and lower bounds)
            df['resistance_zone_upper'] = df['resistance'] * (1 + zone_width)
            df['resistance_zone_lower'] = df['resistance'] * (1 - zone_width)
            
            # Support Zone (upper and lower bounds)
            df['support_zone_upper'] = df['support'] * (1 + zone_width)
            df['support_zone_lower'] = df['support'] * (1 - zone_width)
            
            # Zone strength based on number of touches and time spent
            df['resistance_touches'] = 0
            df['support_touches'] = 0
            df['resistance_zone_strength'] = 0
            df['support_zone_strength'] = 0
            
            for i in range(self.lookback_period, len(df)):
                # Count resistance zone interactions
                resistance_level = df.iloc[i]['resistance']
                if not pd.isna(resistance_level) and resistance_level > 0:
                    recent_highs = df['high'].iloc[i-self.lookback_period:i]
                    recent_lows = df['low'].iloc[i-self.lookback_period:i]
                    recent_closes = df['close'].iloc[i-self.lookback_period:i]
                    
                    # Count touches (price came within zone)
                    zone_upper = resistance_level * (1 + zone_width)
                    zone_lower = resistance_level * (1 - zone_width)
                    
                    touches = ((recent_highs >= zone_lower) & (recent_highs <= zone_upper)).sum()
                    df.at[i, 'resistance_touches'] = touches
                    
                    # Calculate zone strength (touches + time spent in zone)
                    time_in_zone = ((recent_closes >= zone_lower) & (recent_closes <= zone_upper)).sum()
                    rejections = ((recent_highs >= zone_lower) & (recent_closes < zone_lower)).sum()
                    
                    df.at[i, 'resistance_zone_strength'] = touches + time_in_zone + (rejections * 2)
                
                # Count support zone interactions
                support_level = df.iloc[i]['support']
                if not pd.isna(support_level) and support_level > 0:
                    recent_highs = df['high'].iloc[i-self.lookback_period:i]
                    recent_lows = df['low'].iloc[i-self.lookback_period:i]
                    recent_closes = df['close'].iloc[i-self.lookback_period:i]
                    
                    # Count touches (price came within zone)
                    zone_upper = support_level * (1 + zone_width)
                    zone_lower = support_level * (1 - zone_width)
                    
                    touches = ((recent_lows >= zone_lower) & (recent_lows <= zone_upper)).sum()
                    df.at[i, 'support_touches'] = touches
                    
                    # Calculate zone strength
                    time_in_zone = ((recent_closes >= zone_lower) & (recent_closes <= zone_upper)).sum()
                    rejections = ((recent_lows <= zone_upper) & (recent_closes > zone_upper)).sum()
                    
                    df.at[i, 'support_zone_strength'] = touches + time_in_zone + (rejections * 2)
            
            # Zone interaction detection
            df['in_resistance_zone'] = ((df['close'] >= df['resistance_zone_lower']) & 
                                      (df['close'] <= df['resistance_zone_upper']))
            df['in_support_zone'] = ((df['close'] >= df['support_zone_lower']) & 
                                   (df['close'] <= df['support_zone_upper']))
            
            # Previous candle analysis for price action patterns
            df['prev_close'] = df['close'].shift(1)
            df['prev_high'] = df['high'].shift(1)
            df['prev_low'] = df['low'].shift(1)
            df['prev_open'] = df['open'].shift(1)
            
            # Price action confirmation patterns
            df = self._add_price_action_patterns(df)
            
            # Generate signals based on pure price action
            self._generate_price_action_signals(df)
            
            return df
            
        except Exception as e:
            logger.error(f"Error adding price action calculations: {e}")
            return df
    
    def _generate_price_action_signals(self, df):
        """
        Generate signals using Zone-First, Confirmation-Second approach:
        1. Identify strong support/resistance zones
        2. Wait for price to enter these zones
        3. Look for price action confirmation patterns
        4. Generate signals only when both conditions are met
        """
        try:
            # Initialize signal columns
            df['buy_signal'] = False
            df['sell_signal'] = False
            df['hold_signal'] = True
            df['signal_strength'] = 0
            df['signal_reason'] = ''
            
            for i in range(max(self.lookback_period, self.momentum_window, 10), len(df)):
                current = df.iloc[i]
                prev = df.iloc[i-1] if i > 0 else current
                
                # Skip if critical data is missing
                critical_values = [
                    current.get('close', 0),
                    current.get('resistance', 0),
                    current.get('support', 0),
                    current.get('resistance_zone_strength', 0),
                    current.get('support_zone_strength', 0)
                ]
                
                if any(pd.isna(val) or (isinstance(val, (int, float)) and val <= 0 and val != 0) for val in critical_values[:3]):
                    continue
                
                # STEP 1: ZONE ANALYSIS - Identify high-quality zones
                resistance_zone_strength = current.get('resistance_zone_strength', 0)
                support_zone_strength = current.get('support_zone_strength', 0)
                
                # Minimum zone strength required for trading (zones with at least 3 touches/interactions)
                min_zone_strength = 3
                
                # Check if we're in a strong zone
                in_strong_resistance_zone = (current.get('in_resistance_zone', False) and 
                                           resistance_zone_strength >= min_zone_strength)
                in_strong_support_zone = (current.get('in_support_zone', False) and 
                                        support_zone_strength >= min_zone_strength)
                
                # STEP 2: PRICE ACTION CONFIRMATION - Look for confirmation patterns
                buy_score = 0
                sell_score = 0
                signal_reasons = []
                
                # =========================
                # BUY SIGNAL CONDITIONS
                # =========================
                
                # CONDITION 1: SUPPORT ZONE REJECTION (Highest Priority)
                if in_strong_support_zone:
                    # Comprehensive bullish reversal patterns at support
                    reversal_patterns = [
                        ('Strong Bullish Rejection', current.get('bullish_rejection', False), 5),
                        ('Bullish Pin Bar (Hammer)', current.get('pin_bar_bullish', False), 4),
                        ('Bullish Engulfing', current.get('engulfing_bullish', False), 4),
                        ('Morning Star', current.get('morning_star', False), 5),
                        ('Tweezer Bottom', current.get('tweezer_bottom', False), 4),
                        ('Dragonfly Doji', current.get('dragonfly_doji', False), 3),
                        ('Three White Soldiers', current.get('three_white_soldiers', False), 5),
                        ('Bullish Marubozu', current.get('marubozu_bullish', False), 4)
                    ]
                    
                    for pattern_name, pattern_detected, pattern_score in reversal_patterns:
                        if pattern_detected:
                            buy_score += pattern_score
                            signal_reasons.append(f"{pattern_name} at Support Zone (Strength: {support_zone_strength})")
                            break  # Use only the first detected pattern to avoid double-counting
                    
                    # Additional support zone confirmations
                    if current.get('spinning_top', False):
                        buy_score += 2
                        signal_reasons.append(f"Bullish Spinning Top at Support (Strength: {support_zone_strength})")
                
                # CONDITION 2: RESISTANCE ZONE BREAKOUT
                resistance = current.get('resistance', 0)
                if resistance > 0:
                    # Comprehensive bullish breakout patterns
                    breakout_patterns = [
                        ('Strong Resistance Breakout', current.get('bullish_breakout', False), 5),
                        ('Bullish Flag Breakout', current.get('bullish_flag', False), 5),
                        ('Bullish Pennant Breakout', current.get('bullish_pennant', False), 5),
                        ('Bullish Marubozu Breakout', current.get('marubozu_bullish', False), 4),
                        ('Three White Soldiers Breakout', current.get('three_white_soldiers', False), 5)
                    ]
                    
                    for pattern_name, pattern_detected, pattern_score in breakout_patterns:
                        if pattern_detected:
                            buy_score += pattern_score
                            signal_reasons.append(f"{pattern_name} (Level: {resistance:.6f})")
                            break
                    
                    # Volume-confirmed breakout
                    if (current.get('close', 0) > resistance * 1.003 and  # Clear break
                        current.get('close', 0) > current.get('open', 0) and  # Green candle
                        current.get('volume_ratio', 1.0) > 1.5):  # High volume
                        buy_score += 4
                        signal_reasons.append(f"Volume-Confirmed Resistance Break (Level: {resistance:.6f})")
                
                # CONDITION 3: TREND CONTINUATION PATTERNS
                continuation_patterns = [
                    ('Inside Bar Breakout', current.get('inside_bar', False) and prev.get('bullish_breakout', False), 3),
                    ('Outside Bar Bullish', current.get('outside_bar', False) and current.get('close', 0) > current.get('open', 0), 3)
                ]
                
                for pattern_name, pattern_detected, pattern_score in continuation_patterns:
                    if pattern_detected:
                        buy_score += pattern_score
                        signal_reasons.append(pattern_name)
                
                # CONDITION 3: MOMENTUM + ZONE CONFIRMATION
                momentum = current.get('price_momentum', 0)
                if not pd.isna(momentum) and momentum > 0.008:  # Strong positive momentum (0.8%+)
                    # Momentum confirmation near support
                    if current.get('dist_from_support', 1.0) < 0.02:  # Within 2% of support
                        buy_score += 2
                        signal_reasons.append(f"Strong Momentum near Support ({momentum*100:.2f}%)")
                    
                    # Momentum continuation after support bounce
                    elif (prev.get('in_support_zone', False) and 
                          not current.get('in_support_zone', False) and
                          current.get('close', 0) > prev.get('close', 0)):
                        buy_score += 3
                        signal_reasons.append(f"Momentum Continuation after Support Bounce ({momentum*100:.2f}%)")
                
                # =========================
                # SELL SIGNAL CONDITIONS  
                # =========================
                
                # CONDITION 1: RESISTANCE ZONE REJECTION (Highest Priority)
                if in_strong_resistance_zone:
                    # Comprehensive bearish reversal patterns at resistance
                    reversal_patterns = [
                        ('Strong Bearish Rejection', current.get('bearish_rejection', False), 5),
                        ('Bearish Pin Bar (Shooting Star)', current.get('pin_bar_bearish', False), 4),
                        ('Bearish Engulfing', current.get('engulfing_bearish', False), 4),
                        ('Evening Star', current.get('evening_star', False), 5),
                        ('Tweezer Top', current.get('tweezer_top', False), 4),
                        ('Gravestone Doji', current.get('gravestone_doji', False), 3),
                        ('Three Black Crows', current.get('three_black_crows', False), 5),
                        ('Bearish Marubozu', current.get('marubozu_bearish', False), 4)
                    ]
                    
                    for pattern_name, pattern_detected, pattern_score in reversal_patterns:
                        if pattern_detected:
                            sell_score += pattern_score
                            signal_reasons.append(f"{pattern_name} at Resistance Zone (Strength: {resistance_zone_strength})")
                            break  # Use only the first detected pattern to avoid double-counting
                    
                    # Additional resistance zone confirmations
                    if current.get('spinning_bottom', False):
                        sell_score += 2
                        signal_reasons.append(f"Bearish Spinning Bottom at Resistance (Strength: {resistance_zone_strength})")
                
                # CONDITION 2: SUPPORT ZONE BREAKDOWN
                support = current.get('support', 0)
                if support > 0:
                    # Comprehensive bearish breakdown patterns
                    breakdown_patterns = [
                        ('Strong Support Breakdown', current.get('bearish_breakout', False), 5),
                        ('Bearish Flag Breakdown', current.get('bearish_flag', False), 5),
                        ('Bearish Pennant Breakdown', current.get('bearish_pennant', False), 5),
                        ('Bearish Marubozu Breakdown', current.get('marubozu_bearish', False), 4),
                        ('Three Black Crows Breakdown', current.get('three_black_crows', False), 5)
                    ]
                    
                    for pattern_name, pattern_detected, pattern_score in breakdown_patterns:
                        if pattern_detected:
                            sell_score += pattern_score
                            signal_reasons.append(f"{pattern_name} (Level: {support:.6f})")
                            break
                    
                    # Volume-confirmed breakdown
                    if (current.get('close', 0) < support * 0.997 and  # Clear break
                        current.get('close', 0) < current.get('open', 0) and  # Red candle
                        current.get('volume_ratio', 1.0) > 1.5):  # High volume
                        sell_score += 4
                        signal_reasons.append(f"Volume-Confirmed Support Break (Level: {support:.6f})")
                
                # CONDITION 3: TREND CONTINUATION PATTERNS
                continuation_patterns = [
                    ('Inside Bar Breakdown', current.get('inside_bar', False) and prev.get('bearish_breakout', False), 3),
                    ('Outside Bar Bearish', current.get('outside_bar', False) and current.get('close', 0) < current.get('open', 0), 3)
                ]
                
                for pattern_name, pattern_detected, pattern_score in continuation_patterns:
                    if pattern_detected:
                        sell_score += pattern_score
                        signal_reasons.append(pattern_name)
                
                # CONDITION 3: MOMENTUM + ZONE CONFIRMATION
                if not pd.isna(momentum) and momentum < -0.008:  # Strong negative momentum (-0.8%+)
                    # Momentum confirmation near resistance
                    if current.get('dist_from_resistance', 1.0) < 0.02:  # Within 2% of resistance
                        sell_score += 2
                        signal_reasons.append(f"Strong Negative Momentum near Resistance ({momentum*100:.2f}%)")
                    
                    # Momentum continuation after resistance rejection
                    elif (prev.get('in_resistance_zone', False) and 
                          not current.get('in_resistance_zone', False) and
                          current.get('close', 0) < prev.get('close', 0)):
                        sell_score += 3
                        signal_reasons.append(f"Momentum Continuation after Resistance Rejection ({momentum*100:.2f}%)")
                
                # =========================
                # FINAL SIGNAL DECISION
                # =========================
                
                # Require minimum score and clear winner
                min_signal_strength = 4
                
                if buy_score >= min_signal_strength and buy_score > sell_score + 1:
                    df.at[i, 'buy_signal'] = True
                    df.at[i, 'hold_signal'] = False
                    df.at[i, 'signal_strength'] = buy_score
                    df.at[i, 'signal_reason'] = ' | '.join(signal_reasons)
                    
                elif sell_score >= min_signal_strength and sell_score > buy_score + 1:
                    df.at[i, 'sell_signal'] = True
                    df.at[i, 'hold_signal'] = False
                    df.at[i, 'signal_strength'] = sell_score
                    df.at[i, 'signal_reason'] = ' | '.join(signal_reasons)
                
                # Otherwise, HOLD (default state)
                # This happens when:
                # - Not in a strong zone
                # - No clear price action confirmation
                # - Conflicting signals
                # - Insufficient signal strength
            
        except Exception as e:
            logger.error(f"Error generating zone-based price action signals: {e}")
            logger.error(traceback.format_exc())
    
    def get_signal(self, klines):
        """Generate pure price action signals"""
        try:
            min_required = max(self.lookback_period, self.volatility_window, self.momentum_window) + 5
            if not klines or len(klines) < min_required:
                if self._warning_count % 10 == 0:
                    logger.warning(f"Insufficient data for price action signal (need {min_required}, have {len(klines) if klines else 0})")
                self._warning_count += 1
                return None
            
            # Convert and validate data
            df = pd.DataFrame(klines)
            if len(df.columns) != 12:
                logger.error(f"Invalid klines format: expected 12 columns, got {len(df.columns)}")
                return None
                
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 
                         'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore']
            
            # Data cleaning
            numeric_columns = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                if df[col].isna().any():
                    logger.warning(f"Cleaning NaN values in {col}")
                    df[col] = df[col].interpolate(method='linear').bfill().ffill()
            
            # Final validation after cleaning
            if df[numeric_columns].isna().any().any():
                logger.error("Failed to clean price data after interpolation")
                return None
            
            # Add price action calculations
            df = self.add_indicators(df)
            
            if len(df) < 2:
                return None
            
            latest = df.iloc[-1]
            
            # Validate required columns with more lenient checks
            required_columns = ['buy_signal', 'sell_signal', 'hold_signal']
            
            for col in required_columns:
                if col not in df.columns:
                    logger.warning(f"Missing required column: {col}")
                    return None
            
            # Check if we have valid signal data
            if pd.isna(latest.get('buy_signal')) or pd.isna(latest.get('sell_signal')):
                logger.warning("Invalid signal data - NaN values found")
                return None
            
            # Generate signal based on Zone + Price Action confirmation
            signal = None
            
            # BUY Signal: Zone-based bullish confirmation
            if latest.get('buy_signal', False):
                signal = 'BUY'
                signal_strength = latest.get('signal_strength', 0)
                signal_reason = latest.get('signal_reason', '')
                
                logger.info(f"🟢 BUY Signal - Zone-Based Price Action Confirmation")
                logger.info(f"   Signal Strength: {signal_strength}/10")
                logger.info(f"   Signal Reason: {signal_reason}")
                
                # Display zone information
                close_price = latest.get('close', 0)
                resistance = latest.get('resistance', 0)
                support = latest.get('support', 0)
                
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                    
                # Zone strength information
                resistance_strength = latest.get('resistance_zone_strength', 0)
                support_strength = latest.get('support_zone_strength', 0)
                
                if not pd.isna(support) and support > 0:
                    logger.info(f"   Support Zone: {support:.6f} (Strength: {support_strength})")
                if not pd.isna(resistance) and resistance > 0:
                    logger.info(f"   Resistance Zone: {resistance:.6f} (Strength: {resistance_strength})")
                
                # Zone position
                in_support_zone = latest.get('in_support_zone', False)
                in_resistance_zone = latest.get('in_resistance_zone', False)
                
                if in_support_zone:
                    logger.info(f"   📍 PRICE IN SUPPORT ZONE - Looking for bullish rejection")
                elif in_resistance_zone:
                    logger.info(f"   📍 PRICE IN RESISTANCE ZONE - Breakout opportunity")
                
                # Price action patterns
                patterns = []
                
                # Reversal patterns
                if latest.get('bullish_rejection', False):
                    patterns.append("Bullish Zone Rejection")
                if latest.get('pin_bar_bullish', False):
                    patterns.append("Bullish Pin Bar (Hammer)")
                if latest.get('engulfing_bullish', False):
                    patterns.append("Bullish Engulfing")
                if latest.get('morning_star', False):
                    patterns.append("Morning Star")
                if latest.get('tweezer_bottom', False):
                    patterns.append("Tweezer Bottom")
                if latest.get('three_white_soldiers', False):
                    patterns.append("Three White Soldiers")
                if latest.get('marubozu_bullish', False):
                    patterns.append("Bullish Marubozu")
                if latest.get('dragonfly_doji', False):
                    patterns.append("Dragonfly Doji")
                
                # Breakout patterns
                if latest.get('bullish_breakout', False):
                    patterns.append("Bullish Breakout")
                if latest.get('bullish_flag', False):
                    patterns.append("Bullish Flag")
                if latest.get('bullish_pennant', False):
                    patterns.append("Bullish Pennant")
                
                # Continuation patterns
                if latest.get('inside_bar', False):
                    patterns.append("Inside Bar")
                if latest.get('outside_bar', False) and latest.get('close', 0) > latest.get('open', 0):
                    patterns.append("Bullish Outside Bar")
                if latest.get('spinning_top', False):
                    patterns.append("Spinning Top")
                
                if patterns:
                    logger.info(f"   🎯 Confirmed Patterns: {', '.join(patterns)}")
                
                # Momentum and volume
                momentum = latest.get('price_momentum', 0)
                if not pd.isna(momentum):
                    logger.info(f"   📈 Price Momentum: {momentum*100:.2f}%")
                
                if 'volume_ratio' in df.columns:
                    volume_ratio = latest.get('volume_ratio', 1.0)
                    if not pd.isna(volume_ratio):
                        logger.info(f"   📊 Volume Ratio: {volume_ratio:.2f}x average")
            
            # SELL Signal: Zone-based bearish confirmation
            elif latest.get('sell_signal', False):
                signal = 'SELL'
                signal_strength = latest.get('signal_strength', 0)
                signal_reason = latest.get('signal_reason', '')
                
                logger.info(f"🔴 SELL Signal - Zone-Based Price Action Confirmation")
                logger.info(f"   Signal Strength: {signal_strength}/10")
                logger.info(f"   Signal Reason: {signal_reason}")
                
                # Display zone information
                close_price = latest.get('close', 0)
                resistance = latest.get('resistance', 0)
                support = latest.get('support', 0)
                
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                    
                # Zone strength information
                resistance_strength = latest.get('resistance_zone_strength', 0)
                support_strength = latest.get('support_zone_strength', 0)
                
                if not pd.isna(resistance) and resistance > 0:
                    logger.info(f"   Resistance Zone: {resistance:.6f} (Strength: {resistance_strength})")
                if not pd.isna(support) and support > 0:
                    logger.info(f"   Support Zone: {support:.6f} (Strength: {support_strength})")
                
                # Zone position
                in_support_zone = latest.get('in_support_zone', False)
                in_resistance_zone = latest.get('in_resistance_zone', False)
                
                if in_resistance_zone:
                    logger.info(f"   📍 PRICE IN RESISTANCE ZONE - Looking for bearish rejection")
                elif in_support_zone:
                    logger.info(f"   📍 PRICE IN SUPPORT ZONE - Breakdown opportunity")
                
                # Price action patterns
                patterns = []
                
                # Reversal patterns
                if latest.get('bearish_rejection', False):
                    patterns.append("Bearish Zone Rejection")
                if latest.get('pin_bar_bearish', False):
                    patterns.append("Bearish Pin Bar (Shooting Star)")
                if latest.get('engulfing_bearish', False):
                    patterns.append("Bearish Engulfing")
                if latest.get('evening_star', False):
                    patterns.append("Evening Star")
                if latest.get('tweezer_top', False):
                    patterns.append("Tweezer Top")
                if latest.get('three_black_crows', False):
                    patterns.append("Three Black Crows")
                if latest.get('marubozu_bearish', False):
                    patterns.append("Bearish Marubozu")
                if latest.get('gravestone_doji', False):
                    patterns.append("Gravestone Doji")
                
                # Breakdown patterns
                if latest.get('bearish_breakout', False):
                    patterns.append("Bearish Breakout")
                if latest.get('bearish_flag', False):
                    patterns.append("Bearish Flag")
                if latest.get('bearish_pennant', False):
                    patterns.append("Bearish Pennant")
                
                # Continuation patterns
                if latest.get('inside_bar', False):
                    patterns.append("Inside Bar")
                if latest.get('outside_bar', False) and latest.get('close', 0) < latest.get('open', 0):
                    patterns.append("Bearish Outside Bar")
                if latest.get('spinning_bottom', False):
                    patterns.append("Spinning Bottom")
                
                if patterns:
                    logger.info(f"   🎯 Confirmed Patterns: {', '.join(patterns)}")
                
                # Momentum and volume
                momentum = latest.get('price_momentum', 0)
                if not pd.isna(momentum):
                    logger.info(f"   📉 Price Momentum: {momentum*100:.2f}%")
                
                if 'volume_ratio' in df.columns:
                    volume_ratio = latest.get('volume_ratio', 1.0)
                    if not pd.isna(volume_ratio):
                        logger.info(f"   📊 Volume Ratio: {volume_ratio:.2f}x average")
            
            # HOLD Signal: Waiting for zone confirmation
            else:
                signal = 'HOLD'
                logger.info(f"⚪ HOLD Signal - Waiting for Zone + Price Action Confirmation")
                
                close_price = latest.get('close', 0)
                resistance = latest.get('resistance', 0)
                support = latest.get('support', 0)
                
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                
                # Show zone distances
                if not pd.isna(support) and not pd.isna(resistance):
                    logger.info(f"   Support Zone: {support:.6f}")
                    logger.info(f"   Resistance Zone: {resistance:.6f}")
                    
                    dist_to_support = abs(close_price - support) / close_price * 100
                    dist_to_resistance = abs(resistance - close_price) / close_price * 100
                    
                    logger.info(f"   📏 Distance to Support: {dist_to_support:.2f}%")
                    logger.info(f"   📏 Distance to Resistance: {dist_to_resistance:.2f}%")
                
                # Zone strength information
                resistance_strength = latest.get('resistance_zone_strength', 0)
                support_strength = latest.get('support_zone_strength', 0)
                
                if resistance_strength > 0 or support_strength > 0:
                    logger.info(f"   Zone Strengths - Support: {support_strength}, Resistance: {resistance_strength}")
                
                # Current position
                in_support_zone = latest.get('in_support_zone', False)
                in_resistance_zone = latest.get('in_resistance_zone', False)
                
                if in_support_zone:
                    logger.info(f"   📍 Currently in SUPPORT ZONE - Watching for rejection patterns")
                elif in_resistance_zone:
                    logger.info(f"   📍 Currently in RESISTANCE ZONE - Watching for rejection patterns")
                else:
                    logger.info(f"   📍 Price in neutral zone - Waiting for zone approach")
                
                # Momentum info
                momentum = latest.get('price_momentum', 0)
                if not pd.isna(momentum):
                    logger.info(f"   📊 Current Momentum: {momentum*100:.2f}%")
                    logger.info(f"   Resistance Level: {resistance:.6f}")
                
                # Current momentum info
                momentum = latest.get('price_momentum', 0)
                if not pd.isna(momentum):
                    logger.info(f"   📍 Current Momentum: {momentum*100:.2f}%")
            
            # Store signal for history (with safe data)
            timestamp = latest.get('timestamp')
            close_price = latest.get('close', 0)
            momentum = latest.get('price_momentum', 0)
            
            if not pd.isna(timestamp) and not pd.isna(close_price) and not pd.isna(momentum):
                self.signal_history.append({
                    'timestamp': timestamp,
                    'signal': signal,
                    'price': close_price,
                    'momentum': momentum,
                    'signal_strength': latest.get('signal_strength', 0),
                    'signal_reason': latest.get('signal_reason', '')
                })
            
            self.last_signal = signal
            return signal
            
        except Exception as e:
            logger.error(f"Error in price action signal generation: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def analyze_support_resistance_action(self, df, index):
        """
        Analyze support/resistance price action for the current candle
        Returns a dictionary with analysis results
        """
        if index < 1 or index >= len(df):
            return {}
        
        current = df.iloc[index]
        previous = df.iloc[index-1]
        
        analysis = {
            'resistance_breakout': False,
            'resistance_rejection': False,
            'support_breakdown': False,
            'support_rejection': False,
            'action_type': 'none',
            'strength': 0
        }
        
        close_price = current.get('close', 0)
        high_price = current.get('high', 0)
        low_price = current.get('low', 0)
        resistance = current.get('resistance', 0)
        support = current.get('support', 0)
        prev_close = previous.get('close', 0)
        
        # Skip if any critical values are invalid
        if any(pd.isna(val) or val <= 0 for val in [close_price, resistance, support]):
            return analysis
        
        # Resistance Analysis
        if resistance > 0:
            # Resistance Breakout (Close above resistance)
            if close_price > resistance * 1.002:  # 0.2% above resistance
                analysis['resistance_breakout'] = True
                analysis['action_type'] = 'resistance_breakout'
                analysis['strength'] = min(5, int((close_price - resistance) / resistance * 500))
            
            # Resistance Rejection (Touched resistance but closed below)
            elif (high_price >= resistance * 0.998 and  # Touched resistance
                  close_price < resistance * 0.995 and  # Closed significantly below
                  prev_close < resistance):              # Previous was below resistance
                analysis['resistance_rejection'] = True
                analysis['action_type'] = 'resistance_rejection'
                analysis['strength'] = min(5, int((resistance - close_price) / resistance * 500))
        
        # Support Analysis
        if support > 0:
            # Support Breakdown (Close below support)
            if close_price < support * 0.998:  # 0.2% below support
                analysis['support_breakdown'] = True
                analysis['action_type'] = 'support_breakdown'
                analysis['strength'] = min(5, int((support - close_price) / support * 500))
            
            # Support Rejection (Touched support but closed above)
            elif (low_price <= support * 1.002 and   # Touched support
                  close_price > support * 1.005 and  # Closed significantly above
                  prev_close > support):              # Previous was above support
                analysis['support_rejection'] = True
                analysis['action_type'] = 'support_rejection'
                analysis['strength'] = min(5, int((close_price - support) / support * 500))
        
        return analysis

    def _add_price_action_patterns(self, df):
        """Add comprehensive price action confirmation patterns to the dataframe"""
        try:
            # Initialize all pattern columns
            # === REVERSAL PATTERNS ===
            df['pin_bar_bullish'] = False  # Hammer
            df['pin_bar_bearish'] = False  # Shooting Star
            df['engulfing_bullish'] = False
            df['engulfing_bearish'] = False
            df['morning_star'] = False
            df['evening_star'] = False
            df['tweezer_bottom'] = False
            df['tweezer_top'] = False
            df['three_white_soldiers'] = False
            df['three_black_crows'] = False
            df['marubozu_bullish'] = False  # Strong bullish body, no wicks
            df['marubozu_bearish'] = False  # Strong bearish body, no wicks
            
            # === NEUTRAL/INDECISION PATTERNS ===
            df['doji'] = False
            df['gravestone_doji'] = False
            df['dragonfly_doji'] = False
            df['spinning_top'] = False
            df['spinning_bottom'] = False
            
            # === CONTINUATION PATTERNS ===
            df['inside_bar'] = False
            df['outside_bar'] = False
            df['bullish_flag'] = False
            df['bearish_flag'] = False
            df['bullish_pennant'] = False
            df['bearish_pennant'] = False
            
            # === BREAKOUT PATTERNS ===
            df['bullish_breakout'] = False
            df['bearish_breakout'] = False
            
            # === ZONE-SPECIFIC PATTERNS ===
            df['bullish_rejection'] = False
            df['bearish_rejection'] = False
            
            for i in range(3, len(df)):  # Start from index 3 to allow for 3-candle patterns
                current = df.iloc[i]
                prev = df.iloc[i-1]
                prev2 = df.iloc[i-2]
                prev3 = df.iloc[i-3] if i >= 3 else prev2
                
                # Skip if data is invalid
                candles = [current, prev, prev2, prev3]
                valid_data = True
                
                for candle in candles:
                    if any(pd.isna(val) or val <= 0 for val in [
                        candle['open'], candle['high'], candle['low'], candle['close']
                    ]):
                        valid_data = False
                        break
                
                if not valid_data:
                    continue
                
                # Current candle properties
                c_open, c_high, c_low, c_close = current['open'], current['high'], current['low'], current['close']
                c_body = abs(c_close - c_open)
                c_range = c_high - c_low
                c_upper_shadow = c_high - max(c_open, c_close)
                c_lower_shadow = min(c_open, c_close) - c_low
                
                # Previous candle properties
                p_open, p_high, p_low, p_close = prev['open'], prev['high'], prev['low'], prev['close']
                p_body = abs(p_close - p_open)
                p_range = p_high - p_low
                p_upper_shadow = p_high - max(p_open, p_close)
                p_lower_shadow = min(p_open, p_close) - p_low
                
                # Previous 2 candle properties
                p2_open, p2_high, p2_low, p2_close = prev2['open'], prev2['high'], prev2['low'], prev2['close']
                p2_body = abs(p2_close - p2_open)
                p2_range = p2_high - p2_low
                
                # Previous 3 candle properties
                p3_open, p3_high, p3_low, p3_close = prev3['open'], prev3['high'], prev3['low'], prev3['close']
                p3_body = abs(p3_close - p3_open)
                
                # Avoid division by zero
                if c_range == 0 or p_range == 0 or p2_range == 0:
                    continue
                
                # ==============================================
                # SINGLE CANDLE REVERSAL PATTERNS
                # ==============================================
                
                # 1. PIN BAR PATTERNS (Hammer & Shooting Star)
                if c_body > c_range * 0.05:  # Body must be at least 5% of range
                    
                    # BULLISH PIN BAR (Hammer) - Long lower shadow, small body at top
                    if (c_lower_shadow > c_body * 2.5 and  # Lower shadow > 2.5x body
                        c_upper_shadow < c_body * 0.4 and  # Small upper shadow
                        c_body > c_range * 0.1):           # Meaningful body size
                        df.at[i, 'pin_bar_bullish'] = True
                    
                    # BEARISH PIN BAR (Shooting Star) - Long upper shadow, small body at bottom
                    if (c_upper_shadow > c_body * 2.5 and  # Upper shadow > 2.5x body
                        c_lower_shadow < c_body * 0.4 and  # Small lower shadow
                        c_body > c_range * 0.1):           # Meaningful body size
                        df.at[i, 'pin_bar_bearish'] = True
                
                # 2. MARUBOZU PATTERNS (Strong body candles with minimal wicks)
                if c_body > c_range * 0.9:  # Body is 90%+ of total range
                    if c_close > c_open:  # Bullish Marubozu
                        df.at[i, 'marubozu_bullish'] = True
                    else:  # Bearish Marubozu
                        df.at[i, 'marubozu_bearish'] = True
                
                # 3. DOJI PATTERNS (Small body, indecision)
                if c_body < c_range * 0.1 and c_range > 0:  # Body < 10% of range
                    
                    # GRAVESTONE DOJI - Long upper shadow, no lower shadow
                    if (c_upper_shadow > c_range * 0.7 and  # Upper shadow > 70% of range
                        c_lower_shadow < c_range * 0.1):    # Minimal lower shadow
                        df.at[i, 'gravestone_doji'] = True
                    
                    # DRAGONFLY DOJI - Long lower shadow, no upper shadow
                    elif (c_lower_shadow > c_range * 0.7 and  # Lower shadow > 70% of range
                          c_upper_shadow < c_range * 0.1):    # Minimal upper shadow
                        df.at[i, 'dragonfly_doji'] = True
                    
                    # REGULAR DOJI - Small body with shadows on both sides
                    else:
                        df.at[i, 'doji'] = True
                
                # 4. SPINNING TOP/BOTTOM PATTERNS (Small body with long shadows)
                if (c_body > c_range * 0.1 and c_body < c_range * 0.3 and  # Small but meaningful body
                    c_upper_shadow > c_body and c_lower_shadow > c_body):   # Shadows larger than body
                    
                    if c_close > c_open:  # Bullish spinning top
                        df.at[i, 'spinning_top'] = True
                    else:  # Bearish spinning bottom
                        df.at[i, 'spinning_bottom'] = True
                
                # ==============================================
                # TWO CANDLE REVERSAL PATTERNS
                # ==============================================
                
                # 5. ENGULFING PATTERNS (Strong momentum reversal)
                if p_body > 0 and c_body > 0:  # Both candles must have meaningful bodies
                    
                    # BULLISH ENGULFING - Current green candle engulfs previous red
                    if (p_close < p_open and        # Previous red candle
                        c_close > c_open and        # Current green candle
                        c_close > p_open and        # Current close > prev open
                        c_open < p_close and        # Current open < prev close
                        c_body > p_body * 1.1):     # Current body 10% larger
                        df.at[i, 'engulfing_bullish'] = True
                    
                    # BEARISH ENGULFING - Current red candle engulfs previous green
                    elif (p_close > p_open and      # Previous green candle
                          c_close < c_open and      # Current red candle
                          c_close < p_open and      # Current close < prev open
                          c_open > p_close and      # Current open > prev close
                          c_body > p_body * 1.1):   # Current body 10% larger
                        df.at[i, 'engulfing_bearish'] = True
                
                # 6. TWEEZER PATTERNS (Double top/bottom formations)
                if abs(c_high - p_high) < (c_high + p_high) * 0.002:  # Similar highs (0.2% tolerance)
                    # TWEEZER TOP - Both candles touch similar high, bearish reversal
                    if (c_close < c_open and p_close < p_open and  # Both red candles
                        c_high >= max(c_open, c_close) * 1.005):   # High significantly above body
                        df.at[i, 'tweezer_top'] = True
                
                if abs(c_low - p_low) < (c_low + p_low) * 0.002:  # Similar lows (0.2% tolerance)
                    # TWEEZER BOTTOM - Both candles touch similar low, bullish reversal
                    if (c_close > c_open and p_close > p_open and  # Both green candles
                        c_low <= min(c_open, c_close) * 0.995):   # Low significantly below body
                        df.at[i, 'tweezer_bottom'] = True
                
                # ==============================================
                # THREE CANDLE REVERSAL PATTERNS
                # ==============================================
                
                # 7. MORNING STAR (Bullish reversal - bearish, small, bullish)
                if (p2_close < p2_open and          # First candle is bearish
                    p_body < p2_body * 0.5 and      # Second candle has small body
                    c_close > c_open and            # Third candle is bullish
                    c_close > (p2_open + p2_close) / 2):  # Third closes above midpoint of first
                    df.at[i, 'morning_star'] = True
                
                # 8. EVENING STAR (Bearish reversal - bullish, small, bearish)
                if (p2_close > p2_open and          # First candle is bullish
                    p_body < p2_body * 0.5 and      # Second candle has small body
                    c_close < c_open and            # Third candle is bearish
                    c_close < (p2_open + p2_close) / 2):  # Third closes below midpoint of first
                    df.at[i, 'evening_star'] = True
                
                # 9. THREE WHITE SOLDIERS (Strong bullish trend continuation)
                if (p2_close > p2_open and p_close > p_open and c_close > c_open and  # All green
                    c_close > p_close and p_close > p2_close and  # Progressive higher closes
                    c_open > p_open * 0.995 and p_open > p2_open * 0.995 and  # Opens near prev close
                    c_body > c_range * 0.6 and p_body > p_range * 0.6):  # Strong bodies
                    df.at[i, 'three_white_soldiers'] = True
                
                # 10. THREE BLACK CROWS (Strong bearish trend continuation)
                if (p2_close < p2_open and p_close < p_open and c_close < c_open and  # All red
                    c_close < p_close and p_close < p2_close and  # Progressive lower closes
                    c_open < p_open * 1.005 and p_open < p2_open * 1.005 and  # Opens near prev close
                    c_body > c_range * 0.6 and p_body > p_range * 0.6):  # Strong bodies
                    df.at[i, 'three_black_crows'] = True
                
                # ==============================================
                # CONTINUATION PATTERNS
                # ==============================================
                
                # 11. INSIDE BAR (Consolidation pattern)
                if c_high < p_high and c_low > p_low:
                    df.at[i, 'inside_bar'] = True
                
                # 12. OUTSIDE BAR (Volatility expansion)
                if c_high > p_high and c_low < p_low and c_body > p_body:
                    df.at[i, 'outside_bar'] = True
                
                # ==============================================
                # FLAG AND PENNANT PATTERNS (Multi-candle analysis)
                # ==============================================
                
                # 13. FLAG PATTERNS (Need at least 5 candles for proper flag)
                if i >= 5:
                    # Look at last 5 candles for flag pattern
                    flag_candles = df.iloc[i-4:i+1]
                    
                    # Calculate trend direction before flag
                    trend_start = df.iloc[i-7] if i >= 7 else df.iloc[0]
                    pre_flag_move = (prev['close'] - trend_start['close']) / trend_start['close']
                    
                    # Flag criteria: consolidation after strong move
                    if abs(pre_flag_move) > 0.02:  # At least 2% move before flag
                        flag_range = flag_candles['high'].max() - flag_candles['low'].min()
                        flag_body_range = abs(flag_candles['close'].iloc[-1] - flag_candles['open'].iloc[0])
                        
                        # Flag: tight consolidation (range < 50% of previous move)
                        if flag_range < abs(pre_flag_move * prev['close']) * 0.5:
                            
                            # BULLISH FLAG - Uptrend followed by sideways/slight down consolidation
                            if (pre_flag_move > 0 and  # Previous uptrend
                                c_close > c_open and   # Breakout candle is green
                                c_close > flag_candles['high'].max()):  # Breaks above flag
                                df.at[i, 'bullish_flag'] = True
                            
                            # BEARISH FLAG - Downtrend followed by sideways/slight up consolidation
                            elif (pre_flag_move < 0 and  # Previous downtrend
                                  c_close < c_open and   # Breakdown candle is red
                                  c_close < flag_candles['low'].min()):  # Breaks below flag
                                df.at[i, 'bearish_flag'] = True
                
                # 14. PENNANT PATTERNS (Triangular consolidation)
                if i >= 6:
                    # Look at last 6 candles for pennant
                    pennant_candles = df.iloc[i-5:i+1]
                    
                    # Pennant: converging highs and lows
                    early_range = pennant_candles.iloc[0]['high'] - pennant_candles.iloc[0]['low']
                    recent_range = pennant_candles.iloc[-2]['high'] - pennant_candles.iloc[-2]['low']
                    
                    # Pennant criteria: range contraction
                    if recent_range < early_range * 0.7:  # Range contracted by 30%
                        
                        # Calculate pre-pennant trend
                        trend_start = df.iloc[i-9] if i >= 9 else df.iloc[0]
                        pre_pennant_move = (pennant_candles.iloc[0]['close'] - trend_start['close']) / trend_start['close']
                        
                        if abs(pre_pennant_move) > 0.02:  # Significant move before pennant
                            
                            # BULLISH PENNANT
                            if (pre_pennant_move > 0 and  # Previous uptrend
                                c_close > c_open and      # Breakout is green
                                c_close > pennant_candles['high'].max()):  # Breaks above pennant
                                df.at[i, 'bullish_pennant'] = True
                            
                            # BEARISH PENNANT
                            elif (pre_pennant_move < 0 and  # Previous downtrend
                                  c_close < c_open and      # Breakdown is red
                                  c_close < pennant_candles['low'].min()):  # Breaks below pennant
                                df.at[i, 'bearish_pennant'] = True
                
                # ==============================================
                # ZONE-SPECIFIC REJECTION AND BREAKOUT PATTERNS
                # ==============================================
                
                in_support_zone = current.get('in_support_zone', False)
                in_resistance_zone = current.get('in_resistance_zone', False)
                resistance = current.get('resistance', 0)
                support = current.get('support', 0)
                
                # 15. ZONE REJECTION PATTERNS
                if in_support_zone:
                    # Multiple ways to confirm bullish rejection at support
                    bullish_patterns = [
                        df.at[i, 'pin_bar_bullish'],
                        df.at[i, 'engulfing_bullish'],
                        df.at[i, 'tweezer_bottom'],
                        df.at[i, 'morning_star'],
                        df.at[i, 'dragonfly_doji']
                    ]
                    
                    # Strong bounce pattern
                    strong_bounce = (c_low <= prev['low'] and c_close > c_open and 
                                   c_body > c_range * 0.6)
                    
                    if any(bullish_patterns) or strong_bounce:
                        df.at[i, 'bullish_rejection'] = True
                
                if in_resistance_zone:
                    # Multiple ways to confirm bearish rejection at resistance
                    bearish_patterns = [
                        df.at[i, 'pin_bar_bearish'],
                        df.at[i, 'engulfing_bearish'],
                        df.at[i, 'tweezer_top'],
                        df.at[i, 'evening_star'],
                        df.at[i, 'gravestone_doji']
                    ]
                    
                    # Strong rejection pattern
                    strong_rejection = (c_high >= prev['high'] and c_close < c_open and 
                                      c_body > c_range * 0.6)
                    
                    if any(bearish_patterns) or strong_rejection:
                        df.at[i, 'bearish_rejection'] = True
                
                # 16. BREAKOUT CONFIRMATION PATTERNS
                if resistance > 0:
                    # Bullish breakout with strong confirmation
                    breakout_patterns = [
                        df.at[i, 'marubozu_bullish'],
                        df.at[i, 'engulfing_bullish'],
                        df.at[i, 'three_white_soldiers'],
                        df.at[i, 'bullish_flag'],
                        df.at[i, 'bullish_pennant']
                    ]
                    
                    strong_breakout = (c_close > resistance * 1.003 and c_close > c_open and 
                                     c_body > c_range * 0.7 and 
                                     current.get('volume_ratio', 1.0) > 1.3)
                    
                    if any(breakout_patterns) or strong_breakout:
                        df.at[i, 'bullish_breakout'] = True
                
                if support > 0:
                    # Bearish breakdown with strong confirmation
                    breakdown_patterns = [
                        df.at[i, 'marubozu_bearish'],
                        df.at[i, 'engulfing_bearish'],
                        df.at[i, 'three_black_crows'],
                        df.at[i, 'bearish_flag'],
                        df.at[i, 'bearish_pennant']
                    ]
                    
                    strong_breakdown = (c_close < support * 0.997 and c_close < c_open and 
                                      c_body > c_range * 0.7 and 
                                      current.get('volume_ratio', 1.0) > 1.3)
                    
                    if any(breakdown_patterns) or strong_breakdown:
                        df.at[i, 'bearish_breakout'] = True
            
            return df
            
        except Exception as e:
            logger.error(f"Error adding price action patterns: {e}")
            return df


# Factory function to get a strategy by name
def get_strategy(strategy_name):
    """Factory function to get a strategy by name"""
    strategies = {
        'PurePriceActionStrategy': PurePriceActionStrategy(
            lookback_period=PRICE_ACTION_LOOKBACK,
            breakout_threshold=BREAKOUT_THRESHOLD,
            volatility_window=VOLATILITY_WINDOW,
            momentum_window=MOMENTUM_WINDOW,
            sr_strength=SUPPORT_RESISTANCE_STRENGTH
        ),
        # Keep compatibility with old name
        'SmartTrendCatcher': PurePriceActionStrategy(
            lookback_period=PRICE_ACTION_LOOKBACK,
            breakout_threshold=BREAKOUT_THRESHOLD,
            volatility_window=VOLATILITY_WINDOW,
            momentum_window=MOMENTUM_WINDOW,
            sr_strength=SUPPORT_RESISTANCE_STRENGTH
        ),
    }
    
    if strategy_name in strategies:
        return strategies[strategy_name]
    
    logger.warning(f"Strategy {strategy_name} not found. Defaulting to PurePriceActionStrategy.")
    return strategies['PurePriceActionStrategy']


def get_strategy_for_symbol(symbol, strategy_name=None):
    """Get the appropriate strategy based on the trading symbol"""
    # If a specific strategy is requested, use it
    if strategy_name:
        return get_strategy(strategy_name)
    
    # Default to PurePriceActionStrategy for any symbol
    return get_strategy('PurePriceActionStrategy')