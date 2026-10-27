-- KEYS[1]: player:<player_id>
-- KEYS[2]: leaderboard
-- ARGV[1]: player_id
-- ARGV[2]: 배치 클릭 횟수

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

local gold_gained = level * count

local new_score = redis.call(
    "HINCRBY", KEYS[1], "score", gold_gained
)

local total_clicks = redis.call(
    "HINCRBY", KEYS[1], "total_clicks", count
)

redis.call("ZADD", KEYS[2], new_score, ARGV[1])

return {1, new_score, level, total_clicks, gold_gained, count}