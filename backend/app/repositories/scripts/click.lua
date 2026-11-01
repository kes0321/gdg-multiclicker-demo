-- KEYS[1]: player:<player_id>
-- KEYS[2]: leaderboard
-- ARGV[1]: player_id
-- ARGV[2]: Decimal로 생성한 현재 레벨의 보상·비용 JSON

if redis.call("EXISTS", KEYS[1]) == 0 then
    return {0, "PLAYER_NOT_FOUND"}
end

-- 쓰기 전에 필요한 값과 자료형 확인
local score = tonumber(redis.call("HGET", KEYS[1], "score"))
local level = tonumber(redis.call("HGET", KEYS[1], "level"))
local clicks = tonumber(redis.call("HGET", KEYS[1], "total_clicks"))

if not level or level ~= math.floor(level)
    or level < 1 or level > MAX_LEVEL then
    return {0, "INVALID_PLAYER_LEVEL"}
end

local balance = read_balance(level, ARGV[2])
if not balance then
    return {0, "BALANCE_REQUIRED", level}
end
local gold_gained = balance.gold_per_click
if score > MAX_SAFE_INTEGER - gold_gained
    or clicks >= MAX_SAFE_INTEGER then
    return {0, "NUMERIC_LIMIT_EXCEEDED"}
end

if not score or not level or not clicks
    or score < 0 or level < 1 or clicks < 0
    or score ~= math.floor(score)
    or level ~= math.floor(level)
    or clicks ~= math.floor(clicks) then
    return redis.error_reply("INVALID_PLAYER_DATA")
end

local leaderboard_type = redis.call("TYPE", KEYS[2]).ok

if leaderboard_type ~= "none" and leaderboard_type ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end

-- 클릭당 획득 골드는 서버의 레벨 데이터로 결정
local new_score = redis.call(
    "HINCRBY", KEYS[1], "score", string.format("%.0f", gold_gained)
)

local total_clicks = redis.call(
    "HINCRBY", KEYS[1], "total_clicks", 1
)

-- 플레이어 골드와 랭킹 점수를 동일하게 유지
redis.call("ZADD", KEYS[2], new_score, ARGV[1])

return {1, new_score, level, total_clicks, gold_gained}
