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
        MOMENTUM_THRESHOLD,
        VOLATILITY_WINDOW,
        MOMENTUM_WINDOW,
        VOLUME_THRESHOLD
    )
except ImportError:
    # Fallback values for pure price action strategies
    PRICE_ACTION_LOOKBACK = 20
    MOMENTUM_THRESHOLD = 0.01  # 1% momentum threshold
    VOLATILITY_WINDOW = 14
    MOMENTUM_WINDOW = 10
    VOLUME_THRESHOLD = 1.5


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
    Pure Price Action Strategy - Candlestick Pattern & Momentum Based:
    
    ==========================================
    TRADING METHODOLOGY: PATTERN + MOMENTUM + VOLUME
    ==========================================
    
    This strategy focuses exclusively on pure price action without any support/resistance levels:
    
    STEP 1: CANDLESTICK PATTERN RECOGNITION
    - Identifies high-probability reversal patterns (Pin Bars, Engulfing, Stars)
    - Detects continuation patterns (Inside Bars, Outside Bars, Flags)
    - Recognizes momentum patterns (Marubozu, Three Soldiers/Crows)
    - Analyzes indecision patterns (Doji variations, Spinning Tops)
    
    STEP 2: MOMENTUM ANALYSIS
    - Calculates pure price momentum over multiple timeframes
    - Identifies acceleration and deceleration phases
    - Detects momentum divergences and confirmations
    - Measures volatility expansion and contraction
    
    STEP 3: VOLUME CONFIRMATION
    - Confirms patterns with volume analysis
    - Identifies volume spikes during key moves
    - Detects volume divergences
    - Uses volume-weighted momentum calculations
    
    STEP 4: SIGNAL GENERATION
    - Combines pattern strength with momentum direction
    - Requires multiple confirmations for signal generation
    - Uses dynamic scoring system (minimum 4/10 strength)
    - Provides detailed reasoning for each signal
    
    ==========================================
    SIGNAL TYPES & CONDITIONS
    ==========================================
    
    🟢 BUY SIGNALS:
    1. BULLISH REVERSAL PATTERNS
       - Strong bullish pin bars (hammer, dragonfly doji)
       - Bullish engulfing patterns with volume
       - Morning star formations
       - Three white soldiers momentum
    
    2. BULLISH CONTINUATION PATTERNS
       - Bullish flags after momentum moves
       - Inside bar breakouts to upside
       - Outside bars with bullish close
       - Marubozu candles in uptrend
    
    3. MOMENTUM CONFIRMATION
       - Strong positive momentum (>1%)
       - Momentum acceleration
       - Volume-confirmed moves
       - Volatility expansion on bullish moves
    
    🔴 SELL SIGNALS:
    1. BEARISH REVERSAL PATTERNS
       - Strong bearish pin bars (shooting star, gravestone doji)
       - Bearish engulfing patterns with volume
       - Evening star formations
       - Three black crows momentum
    
    2. BEARISH CONTINUATION PATTERNS
       - Bearish flags after momentum moves
       - Inside bar breakouts to downside
       - Outside bars with bearish close
       - Bearish marubozu in downtrend
    
    3. MOMENTUM CONFIRMATION
       - Strong negative momentum (<-1%)
       - Momentum acceleration to downside
       - Volume-confirmed moves
       - Volatility expansion on bearish moves
    
    ⚪ HOLD SIGNALS:
    - Weak or conflicting patterns
    - Low momentum periods
    - Indecision patterns without clear direction
    - Insufficient signal strength (< 4/10)
    
    ==========================================
    BENEFITS OF PURE PRICE ACTION APPROACH
    ==========================================
    
    ✅ Universal Application:
    - Works on any timeframe and market condition
    - No dependency on historical levels
    - Adapts to changing market dynamics
    
    ✅ Real-Time Responsiveness:
    - Immediate pattern recognition
    - Quick momentum detection
    - Responsive to market sentiment changes
    
    ✅ Clean Signal Generation:
    - Clear entry/exit criteria based on patterns
    - Objective pattern recognition algorithms
    - Momentum-based confirmation system
    
    ✅ Risk Management:
    - Pattern-based stop loss placement
    - Dynamic position sizing based on volatility
    - Clear reward-to-risk ratios per pattern type
    """
    
    def __init__(self, 
                 lookback_period=20,        # Lookback period for analysis
                 momentum_threshold=0.01,   # 1% momentum threshold for signals
                 volatility_window=14,      # Volatility calculation window
                 momentum_window=10,        # Momentum calculation window
                 volume_threshold=1.5):     # Volume spike threshold
        
        super().__init__("PurePriceActionStrategy")
        
        # Parameter validation
        if lookback_period <= 0:
            raise ValueError("Lookback period must be positive")
        if momentum_threshold <= 0:
            raise ValueError("Momentum threshold must be positive") 
        if volatility_window <= 0:
            raise ValueError("Volatility window must be positive")
        if momentum_window <= 0:
            raise ValueError("Momentum window must be positive")
        if volume_threshold <= 0:
            raise ValueError("Volume threshold must be positive")
        
        # Store parameters
        self.lookback_period = lookback_period
        self.momentum_threshold = momentum_threshold
        self.volatility_window = volatility_window
        self.momentum_window = momentum_window
        self.volume_threshold = volume_threshold
        self._warning_count = 0
        
        logger.info(f"{self.name} initialized with:")
        logger.info(f"  Lookback Period: {lookback_period} candles")
        logger.info(f"  Momentum Threshold: {momentum_threshold*100}%")
        logger.info(f"  Volatility Window: {volatility_window} periods")
        logger.info(f"  Momentum Window: {momentum_window} periods")
        logger.info(f"  Volume Threshold: {volume_threshold}x average")
    
    def add_indicators(self, df):
        """Add pure price action calculations without support/resistance dependencies"""
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
            
            # === PURE MOMENTUM CALCULATIONS ===
            
            # Short-term momentum (fast signals)
            df['momentum_fast'] = df['close'].pct_change(periods=self.momentum_window//2)
            
            # Medium-term momentum (trend signals)
            df['price_momentum'] = df['close'].pct_change(periods=self.momentum_window)
            
            # Long-term momentum (trend confirmation)
            df['momentum_slow'] = df['close'].pct_change(periods=self.momentum_window*2)
            
            # Momentum acceleration (change in momentum)
            df['momentum_acceleration'] = df['price_momentum'].diff()
            
            # === VOLATILITY ANALYSIS ===
            
            # True Range for volatility
            df['high_low'] = df['high'] - df['low']
            df['high_close'] = abs(df['high'] - df['close'].shift(1))
            df['low_close'] = abs(df['low'] - df['close'].shift(1))
            df['true_range'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
            
            # Average True Range (ATR) for volatility measurement
            df['atr'] = df['true_range'].rolling(window=self.volatility_window).mean()
            
            # Price returns and volatility
            df['returns'] = df['close'].pct_change()
            df['volatility'] = df['returns'].rolling(window=self.volatility_window).std()
            
            # Volatility expansion/contraction
            df['volatility_ratio'] = df['volatility'] / df['volatility'].rolling(window=self.lookback_period).mean()
            
            # === CANDLESTICK BODY ANALYSIS ===
            
            # Basic candle properties
            df['body_size'] = abs(df['close'] - df['open'])
            df['upper_shadow'] = df['high'] - np.maximum(df['open'], df['close'])
            df['lower_shadow'] = np.minimum(df['open'], df['close']) - df['low']
            df['total_range'] = df['high'] - df['low']
            
            # Relative measurements (protect against division by zero)
            df['body_ratio'] = np.where(
                df['total_range'] != 0,
                df['body_size'] / df['total_range'],
                0.5
            )
            
            df['upper_shadow_ratio'] = np.where(
                df['total_range'] != 0,
                df['upper_shadow'] / df['total_range'],
                0.0
            )
            
            df['lower_shadow_ratio'] = np.where(
                df['total_range'] != 0,
                df['lower_shadow'] / df['total_range'],
                0.0
            )
            
            # Candle direction
            df['is_bullish'] = df['close'] > df['open']
            df['is_bearish'] = df['close'] < df['open']
            df['is_doji'] = df['body_ratio'] < 0.1  # Very small body
            
            # === VOLUME ANALYSIS (if available) ===
            
            if 'volume' in df.columns:
                # Volume moving averages
                df['avg_volume'] = df['volume'].rolling(window=self.lookback_period).mean()
                
                # Volume ratio (current vs average)
                df['volume_ratio'] = np.where(
                    df['avg_volume'] != 0,
                    df['volume'] / df['avg_volume'],
                    1.0
                )
                
                # Volume-weighted momentum
                df['volume_momentum'] = df['price_momentum'] * df['volume_ratio']
                
                # Volume spikes
                df['volume_spike'] = df['volume_ratio'] > self.volume_threshold
                
            else:
                # Default volume values if volume data not available
                df['volume_ratio'] = 1.0
                df['volume_momentum'] = df['price_momentum']
                df['volume_spike'] = False
            
            # === PRICE PATTERN ANALYSIS ===
            
            # Moving averages for trend context
            df['ma_fast'] = df['close'].rolling(window=self.momentum_window).mean()
            df['ma_slow'] = df['close'].rolling(window=self.momentum_window*2).mean()
            
            # Price position relative to moving averages
            df['above_ma_fast'] = df['close'] > df['ma_fast']
            df['above_ma_slow'] = df['close'] > df['ma_slow']
            
            # Trend direction
            df['trend_bullish'] = (df['ma_fast'] > df['ma_slow']) & df['above_ma_fast']
            df['trend_bearish'] = (df['ma_fast'] < df['ma_slow']) & ~df['above_ma_fast']
            
            # === MOMENTUM SIGNALS ===
            
            # Strong momentum conditions
            df['strong_bullish_momentum'] = (
                (df['price_momentum'] > self.momentum_threshold) &
                (df['momentum_acceleration'] > 0) &
                df['is_bullish']
            )
            
            df['strong_bearish_momentum'] = (
                (df['price_momentum'] < -self.momentum_threshold) &
                (df['momentum_acceleration'] < 0) &
                df['is_bearish']
            )
            
            # Momentum divergence detection
            df['momentum_bullish_div'] = (
                (df['close'] < df['close'].shift(1)) &
                (df['price_momentum'] > df['price_momentum'].shift(1))
            )
            
            df['momentum_bearish_div'] = (
                (df['close'] > df['close'].shift(1)) &
                (df['price_momentum'] < df['price_momentum'].shift(1))
            )
            
            # === PREVIOUS CANDLE ANALYSIS ===
            
            # Previous candle data for pattern recognition
            df['prev_close'] = df['close'].shift(1)
            df['prev_high'] = df['high'].shift(1)
            df['prev_low'] = df['low'].shift(1)
            df['prev_open'] = df['open'].shift(1)
            df['prev_body_size'] = df['body_size'].shift(1)
            df['prev_is_bullish'] = df['is_bullish'].shift(1)
            df['prev_is_bearish'] = df['is_bearish'].shift(1)
            
            # Add candlestick pattern recognition
            df = self._add_price_action_patterns(df)
            
            # Generate pure price action signals
            self._generate_pure_price_action_signals(df)
            
            return df
            
        except Exception as e:
            logger.error(f"Error adding pure price action calculations: {e}")
            return df
    
    def _generate_pure_price_action_signals(self, df):
        """
        Generate signals using Pure Price Action approach:
        1. Identify strong candlestick patterns
        2. Confirm with momentum analysis
        3. Validate with volume (if available)
        4. Generate signals based on pattern + momentum + volume confluence
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
                    current.get('price_momentum', 0),
                    current.get('volatility', 0)
                ]
                
                if any(pd.isna(val) for val in critical_values):
                    continue
                
                # Initialize scoring
                buy_score = 0
                sell_score = 0
                signal_reasons = []
                
                # Current price action data
                close_price = current.get('close', 0)
                momentum = current.get('price_momentum', 0)
                momentum_fast = current.get('momentum_fast', 0)
                momentum_slow = current.get('momentum_slow', 0)
                momentum_accel = current.get('momentum_acceleration', 0)
                volatility_ratio = current.get('volatility_ratio', 1.0)
                volume_ratio = current.get('volume_ratio', 1.0)
                volume_spike = current.get('volume_spike', False)
                
                # Trend context
                trend_bullish = current.get('trend_bullish', False)
                trend_bearish = current.get('trend_bearish', False)
                
                # =========================
                # BUY SIGNAL CONDITIONS
                # =========================
                
                # CONDITION 1: STRONG BULLISH REVERSAL PATTERNS
                bullish_reversal_patterns = [
                    ('Bullish Pin Bar (Hammer)', current.get('pin_bar_bullish', False), 4),
                    ('Bullish Engulfing', current.get('engulfing_bullish', False), 5),
                    ('Morning Star', current.get('morning_star', False), 5),
                    ('Dragonfly Doji', current.get('dragonfly_doji', False), 3),
                    ('Tweezer Bottom', current.get('tweezer_bottom', False), 4),
                    ('Three White Soldiers', current.get('three_white_soldiers', False), 5),
                    ('Bullish Marubozu', current.get('marubozu_bullish', False), 4)
                ]
                
                for pattern_name, pattern_detected, base_score in bullish_reversal_patterns:
                    if pattern_detected:
                        score = base_score
                        
                        # Boost score with volume confirmation
                        if volume_spike:
                            score += 2
                            pattern_name += " + Volume Spike"
                        
                        # Boost score with volatility expansion
                        if volatility_ratio > 1.2:
                            score += 1
                            pattern_name += " + Vol Expansion"
                        
                        buy_score += score
                        signal_reasons.append(pattern_name)
                        break  # Use only first detected reversal pattern
                
                # CONDITION 2: STRONG BULLISH CONTINUATION PATTERNS
                bullish_continuation_patterns = [
                    ('Bullish Flag', current.get('bullish_flag', False), 4),
                    ('Bullish Pennant', current.get('bullish_pennant', False), 4),
                    ('Inside Bar Bullish Breakout', current.get('inside_bar', False) and 
                     current.get('close', 0) > current.get('open', 0) and momentum > 0, 3),
                    ('Outside Bar Bullish', current.get('outside_bar', False) and 
                     current.get('close', 0) > current.get('open', 0), 3)
                ]
                
                for pattern_name, pattern_detected, base_score in bullish_continuation_patterns:
                    if pattern_detected:
                        score = base_score
                        
                        # Require trend alignment for continuation patterns
                        if trend_bullish:
                            score += 2
                            pattern_name += " + Bullish Trend"
                        
                        # Volume confirmation
                        if volume_spike:
                            score += 1
                            pattern_name += " + Volume"
                        
                        buy_score += score
                        signal_reasons.append(pattern_name)
                
                # CONDITION 3: STRONG MOMENTUM SIGNALS
                if not pd.isna(momentum) and not pd.isna(momentum_accel):
                    # Strong positive momentum
                    if momentum > self.momentum_threshold:
                        momentum_score = 2
                        momentum_reason = f"Strong Bullish Momentum ({momentum*100:.2f}%)"
                        
                        # Momentum acceleration
                        if momentum_accel > 0:
                            momentum_score += 2
                            momentum_reason += " + Acceleration"
                        
                        # Multi-timeframe momentum alignment
                        if (not pd.isna(momentum_fast) and not pd.isna(momentum_slow) and
                            momentum_fast > 0 and momentum_slow > 0):
                            momentum_score += 2
                            momentum_reason += " + Multi-TF Alignment"
                        
                        # Volume-weighted momentum
                        if volume_ratio > self.volume_threshold:
                            momentum_score += 2
                            momentum_reason += " + Volume Confirmation"
                        
                        buy_score += momentum_score
                        signal_reasons.append(momentum_reason)
                
                # CONDITION 4: MOMENTUM DIVERGENCE BULLISH
                if current.get('momentum_bullish_div', False):
                    buy_score += 3
                    signal_reasons.append("Bullish Momentum Divergence")
                
                # CONDITION 5: VOLATILITY EXPANSION ON BULLISH MOVES
                if (volatility_ratio > 1.3 and momentum > 0 and 
                    current.get('close', 0) > current.get('open', 0)):
                    buy_score += 2
                    signal_reasons.append(f"Volatility Expansion on Bullish Move ({volatility_ratio:.1f}x)")
                
                # =========================
                # SELL SIGNAL CONDITIONS  
                # =========================
                
                # CONDITION 1: STRONG BEARISH REVERSAL PATTERNS
                bearish_reversal_patterns = [
                    ('Bearish Pin Bar (Shooting Star)', current.get('pin_bar_bearish', False), 4),
                    ('Bearish Engulfing', current.get('engulfing_bearish', False), 5),
                    ('Evening Star', current.get('evening_star', False), 5),
                    ('Gravestone Doji', current.get('gravestone_doji', False), 3),
                    ('Tweezer Top', current.get('tweezer_top', False), 4),
                    ('Three Black Crows', current.get('three_black_crows', False), 5),
                    ('Bearish Marubozu', current.get('marubozu_bearish', False), 4)
                ]
                
                for pattern_name, pattern_detected, base_score in bearish_reversal_patterns:
                    if pattern_detected:
                        score = base_score
                        
                        # Boost score with volume confirmation
                        if volume_spike:
                            score += 2
                            pattern_name += " + Volume Spike"
                        
                        # Boost score with volatility expansion
                        if volatility_ratio > 1.2:
                            score += 1
                            pattern_name += " + Vol Expansion"
                        
                        sell_score += score
                        signal_reasons.append(pattern_name)
                        break  # Use only first detected reversal pattern
                
                # CONDITION 2: STRONG BEARISH CONTINUATION PATTERNS
                bearish_continuation_patterns = [
                    ('Bearish Flag', current.get('bearish_flag', False), 4),
                    ('Bearish Pennant', current.get('bearish_pennant', False), 4),
                    ('Inside Bar Bearish Breakout', current.get('inside_bar', False) and 
                     current.get('close', 0) < current.get('open', 0) and momentum < 0, 3),
                    ('Outside Bar Bearish', current.get('outside_bar', False) and 
                     current.get('close', 0) < current.get('open', 0), 3)
                ]
                
                for pattern_name, pattern_detected, base_score in bearish_continuation_patterns:
                    if pattern_detected:
                        score = base_score
                        
                        # Require trend alignment for continuation patterns
                        if trend_bearish:
                            score += 2
                            pattern_name += " + Bearish Trend"
                        
                        # Volume confirmation
                        if volume_spike:
                            score += 1
                            pattern_name += " + Volume"
                        
                        sell_score += score
                        signal_reasons.append(pattern_name)
                
                # CONDITION 3: STRONG MOMENTUM SIGNALS
                if not pd.isna(momentum) and not pd.isna(momentum_accel):
                    # Strong negative momentum
                    if momentum < -self.momentum_threshold:
                        momentum_score = 2
                        momentum_reason = f"Strong Bearish Momentum ({momentum*100:.2f}%)"
                        
                        # Momentum acceleration (to downside)
                        if momentum_accel < 0:
                            momentum_score += 2
                            momentum_reason += " + Acceleration"
                        
                        # Multi-timeframe momentum alignment
                        if (not pd.isna(momentum_fast) and not pd.isna(momentum_slow) and
                            momentum_fast < 0 and momentum_slow < 0):
                            momentum_score += 2
                            momentum_reason += " + Multi-TF Alignment"
                        
                        # Volume-weighted momentum
                        if volume_ratio > self.volume_threshold:
                            momentum_score += 2
                            momentum_reason += " + Volume Confirmation"
                        
                        sell_score += momentum_score
                        signal_reasons.append(momentum_reason)
                
                # CONDITION 4: MOMENTUM DIVERGENCE BEARISH
                if current.get('momentum_bearish_div', False):
                    sell_score += 3
                    signal_reasons.append("Bearish Momentum Divergence")
                
                # CONDITION 5: VOLATILITY EXPANSION ON BEARISH MOVES
                if (volatility_ratio > 1.3 and momentum < 0 and 
                    current.get('close', 0) < current.get('open', 0)):
                    sell_score += 2
                    signal_reasons.append(f"Volatility Expansion on Bearish Move ({volatility_ratio:.1f}x)")
                
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
                # - No clear patterns detected
                # - Weak momentum
                # - Conflicting signals
                # - Insufficient signal strength
            
        except Exception as e:
            logger.error(f"Error generating pure price action signals: {e}")
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
            
            # Generate signal based on Pure Price Action
            signal = None
            
            # BUY Signal: Pure price action bullish confirmation
            if latest.get('buy_signal', False):
                signal = 'BUY'
                signal_strength = latest.get('signal_strength', 0)
                signal_reason = latest.get('signal_reason', '')
                
                logger.info(f"🟢 BUY Signal - Pure Price Action Confirmation")
                logger.info(f"   Signal Strength: {signal_strength}/10")
                logger.info(f"   Signal Reason: {signal_reason}")
                
                # Display current price and momentum
                close_price = latest.get('close', 0)
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                
                # Price action patterns
                patterns = []
                
                # Reversal patterns
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
                
                # Continuation patterns
                if latest.get('bullish_flag', False):
                    patterns.append("Bullish Flag")
                if latest.get('bullish_pennant', False):
                    patterns.append("Bullish Pennant")
                if latest.get('inside_bar', False):
                    patterns.append("Inside Bar")
                if latest.get('outside_bar', False) and latest.get('close', 0) > latest.get('open', 0):
                    patterns.append("Bullish Outside Bar")
                if latest.get('spinning_top', False):
                    patterns.append("Spinning Top")
                
                if patterns:
                    logger.info(f"   🎯 Detected Patterns: {', '.join(patterns)}")
                
                # Momentum analysis
                momentum = latest.get('price_momentum', 0)
                momentum_fast = latest.get('momentum_fast', 0)
                momentum_slow = latest.get('momentum_slow', 0)
                
                if not pd.isna(momentum):
                    logger.info(f"   📈 Price Momentum: {momentum*100:.2f}%")
                
                if not pd.isna(momentum_fast) and not pd.isna(momentum_slow):
                    logger.info(f"   📊 Fast Momentum: {momentum_fast*100:.2f}%")
                    logger.info(f"   📊 Slow Momentum: {momentum_slow*100:.2f}%")
                
                # Volatility and volume
                volatility_ratio = latest.get('volatility_ratio', 1.0)
                if not pd.isna(volatility_ratio):
                    logger.info(f"   📊 Volatility Ratio: {volatility_ratio:.2f}x")
                
                if 'volume_ratio' in df.columns:
                    volume_ratio = latest.get('volume_ratio', 1.0)
                    if not pd.isna(volume_ratio):
                        logger.info(f"   📊 Volume Ratio: {volume_ratio:.2f}x average")
                
                # Trend context
                if latest.get('trend_bullish', False):
                    logger.info(f"   📈 Trend Context: BULLISH")
                elif latest.get('trend_bearish', False):
                    logger.info(f"   📉 Trend Context: BEARISH")
                else:
                    logger.info(f"   ➡️ Trend Context: NEUTRAL")
            
            # SELL Signal: Pure price action bearish confirmation
            elif latest.get('sell_signal', False):
                signal = 'SELL'
                signal_strength = latest.get('signal_strength', 0)
                signal_reason = latest.get('signal_reason', '')
                
                logger.info(f"🔴 SELL Signal - Pure Price Action Confirmation")
                logger.info(f"   Signal Strength: {signal_strength}/10")
                logger.info(f"   Signal Reason: {signal_reason}")
                
                # Display current price and momentum
                close_price = latest.get('close', 0)
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                
                # Price action patterns
                patterns = []
                
                # Reversal patterns
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
                
                # Continuation patterns
                if latest.get('bearish_flag', False):
                    patterns.append("Bearish Flag")
                if latest.get('bearish_pennant', False):
                    patterns.append("Bearish Pennant")
                if latest.get('inside_bar', False):
                    patterns.append("Inside Bar")
                if latest.get('outside_bar', False) and latest.get('close', 0) < latest.get('open', 0):
                    patterns.append("Bearish Outside Bar")
                if latest.get('spinning_bottom', False):
                    patterns.append("Spinning Bottom")
                
                if patterns:
                    logger.info(f"   🎯 Detected Patterns: {', '.join(patterns)}")
                
                # Momentum analysis
                momentum = latest.get('price_momentum', 0)
                momentum_fast = latest.get('momentum_fast', 0)
                momentum_slow = latest.get('momentum_slow', 0)
                
                if not pd.isna(momentum):
                    logger.info(f"   📉 Price Momentum: {momentum*100:.2f}%")
                
                if not pd.isna(momentum_fast) and not pd.isna(momentum_slow):
                    logger.info(f"   📊 Fast Momentum: {momentum_fast*100:.2f}%")
                    logger.info(f"   📊 Slow Momentum: {momentum_slow*100:.2f}%")
                
                # Volatility and volume
                volatility_ratio = latest.get('volatility_ratio', 1.0)
                if not pd.isna(volatility_ratio):
                    logger.info(f"   📊 Volatility Ratio: {volatility_ratio:.2f}x")
                
                if 'volume_ratio' in df.columns:
                    volume_ratio = latest.get('volume_ratio', 1.0)
                    if not pd.isna(volume_ratio):
                        logger.info(f"   📊 Volume Ratio: {volume_ratio:.2f}x average")
                
                # Trend context
                if latest.get('trend_bullish', False):
                    logger.info(f"   📈 Trend Context: BULLISH")
                elif latest.get('trend_bearish', False):
                    logger.info(f"   📉 Trend Context: BEARISH")
                else:
                    logger.info(f"   ➡️ Trend Context: NEUTRAL")
            
            # HOLD Signal: Waiting for clear price action patterns
            else:
                signal = 'HOLD'
                logger.info(f"⚪ HOLD Signal - Waiting for Clear Price Action Patterns")
                
                close_price = latest.get('close', 0)
                if not pd.isna(close_price):
                    logger.info(f"   Current Price: {close_price:.6f}")
                
                # Current momentum info
                momentum = latest.get('price_momentum', 0)
                if not pd.isna(momentum):
                    logger.info(f"   📊 Current Momentum: {momentum*100:.2f}%")
                    
                    if abs(momentum) < self.momentum_threshold:
                        logger.info(f"   ⏳ Momentum below threshold ({self.momentum_threshold*100:.1f}%)")
                
                # Trend info
                if latest.get('trend_bullish', False):
                    logger.info(f"   📊 Trend Context: BULLISH - Waiting for bullish patterns")
                elif latest.get('trend_bearish', False):
                    logger.info(f"   📊 Trend Context: BEARISH - Waiting for bearish patterns")
                else:
                    logger.info(f"   ➡️ Trend Context: NEUTRAL - Waiting for directional clarity")
                
                # Pattern status
                any_patterns = any([
                    latest.get('pin_bar_bullish', False),
                    latest.get('pin_bar_bearish', False),
                    latest.get('engulfing_bullish', False),
                    latest.get('engulfing_bearish', False),
                    latest.get('morning_star', False),
                    latest.get('evening_star', False),
                    latest.get('doji', False)
                ])
                
                if not any_patterns:
                    logger.info(f"   ⏳ No significant patterns detected")
                else:
                    logger.info(f"   � Patterns detected but insufficient strength for signal")
            
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
                # ENHANCED PATTERN CONFIRMATION
                # ==============================================
                
                # No support/resistance zones - use pure price action patterns only
                # Pattern confirmation based on candle strength and momentum
                
                # Strong bullish patterns get additional confirmation
                bullish_patterns = [
                    df.at[i, 'pin_bar_bullish'],
                    df.at[i, 'engulfing_bullish'],
                    df.at[i, 'tweezer_bottom'],
                    df.at[i, 'morning_star'],
                    df.at[i, 'dragonfly_doji'],
                    df.at[i, 'three_white_soldiers']
                ]
                
                # Strong bearish patterns get additional confirmation
                bearish_patterns = [
                    df.at[i, 'pin_bar_bearish'],
                    df.at[i, 'engulfing_bearish'],
                    df.at[i, 'tweezer_top'],
                    df.at[i, 'evening_star'],
                    df.at[i, 'gravestone_doji'],
                    df.at[i, 'three_black_crows']
                ]
                
                # Momentum-based pattern confirmation
                momentum = current.get('price_momentum', 0)
                volume_ratio = current.get('volume_ratio', 1.0)
                
                # Enhanced bullish confirmation
                if any(bullish_patterns):
                    # Add momentum and volume confirmation
                    strong_bullish_confluence = (
                        momentum > 0.005 and  # Positive momentum
                        volume_ratio > 1.2 and  # Above average volume
                        c_close > c_open  # Green candle
                    )
                    if strong_bullish_confluence:
                        df.at[i, 'bullish_rejection'] = True
                
                # Enhanced bearish confirmation
                if any(bearish_patterns):
                    # Add momentum and volume confirmation
                    strong_bearish_confluence = (
                        momentum < -0.005 and  # Negative momentum
                        volume_ratio > 1.2 and  # Above average volume
                        c_close < c_open  # Red candle
                    )
                    if strong_bearish_confluence:
                        df.at[i, 'bearish_rejection'] = True
            
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
            momentum_threshold=MOMENTUM_THRESHOLD,
            volatility_window=VOLATILITY_WINDOW,
            momentum_window=MOMENTUM_WINDOW,
            volume_threshold=VOLUME_THRESHOLD
        ),
        # Keep compatibility with old name
        'SmartTrendCatcher': PurePriceActionStrategy(
            lookback_period=PRICE_ACTION_LOOKBACK,
            momentum_threshold=MOMENTUM_THRESHOLD,
            volatility_window=VOLATILITY_WINDOW,
            momentum_window=MOMENTUM_WINDOW,
            volume_threshold=VOLUME_THRESHOLD
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