-- KEYS[1]: player:<player_id>
-- KEYS[2]: leaderboard
-- KEYS[3]: rate_limit:click:<player_id>
-- ARGV[1]: player_id
-- ARGV[2]: 배치 클릭 횟수
-- ARGV[3]: Decimal로 생성한 현재 레벨의 보상·비용 JSON

local count = tonumber(ARGV[2])

-- Redis에서도 방어적으로 검사
if not count or count ~= math.floor(count)
    or count < 1 or count > 10 then
    return {0, "INVALID_BATCH_COUNT"}
end

if redis.call("EXISTS", KEYS[1]) == 0 then
    return {0, "PLAYER_NOT_FOUND"}
end

local score = tonumber(redis.call("HGET", KEYS[1], "score"))
local level = tonumber(redis.call("HGET", KEYS[1], "level"))
local clicks = tonumber(redis.call("HGET", KEYS[1], "total_clicks"))

if not level or level ~= math.floor(level)
    or level < 1 or level > MAX_LEVEL then
    return {0, "INVALID_PLAYER_LEVEL"}
end

if not score or not level or not clicks
    or score < 0 or level < 1 or clicks < 0
    or score ~= math.floor(score)
    or level ~= math.floor(level)
    or clicks ~= math.floor(clicks) then
    return redis.error_reply("INVALID_PLAYER_DATA")
end

local balance = read_balance(level, ARGV[3])
if not balance then
    return {0, "BALANCE_REQUIRED", level}
end

local gold_gained = balance.gold_per_click * count
if score > MAX_SAFE_INTEGER - gold_gained
    or clicks > MAX_SAFE_INTEGER - count then
    return {0, "NUMERIC_LIMIT_EXCEEDED"}
end

local leaderboard_type = redis.call("TYPE", KEYS[2]).ok

if leaderboard_type ~= "none" and leaderboard_type ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end


local capacity = 30
local bucket_ttl = 60

local raw_tokens = redis.call("HGET", KEYS[3], "tokens")
local tokens

if raw_tokens then
    tokens = tonumber(raw_tokens)

    if not tokens or tokens < 0 or tokens > capacity
        or tokens ~= math.floor(tokens) then
        return redis.error_reply("INVALID_BUCKET_DATA")
    end
else
    -- 60초 비활동으로 버킷이 만료된 경우 초기화
    -- 기존 버킷에 대한 시간 기반 충전은 하지 않음
    tokens = capacity
end

-- 일부 클릭만 승인하지 않고 배치 전체 거절
if tokens < count then
    return {0, "RATE_LIMITED", tokens}
end

-- 버킷이 없을 때만 생성 시각 기록
if not raw_tokens then
    local now = redis.call("TIME")
    local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)

    redis.call(
        "HSET", KEYS[3],
        "last_refill_ms", now_ms
    )
end

redis.call("HSET", KEYS[3], "tokens", tokens - count)
redis.call("EXPIRE", KEYS[3], bucket_ttl)

-- 아래에는 기존 골드·클릭 수·랭킹 갱신 코드 유지

local new_score = redis.call(
    "HINCRBY", KEYS[1], "score", string.format("%.0f", gold_gained)
)

local total_clicks = redis.call(
    "HINCRBY", KEYS[1], "total_clicks", count
)

redis.call("ZADD", KEYS[2], new_score, ARGV[1])

return {1, new_score, level, total_clicks, gold_gained, count}
