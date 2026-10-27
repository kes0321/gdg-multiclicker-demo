-- KEYS[1]: player:<player_id>
-- KEYS[2]: leaderboard
-- ARGV[1]: player_id

if redis.call("EXISTS", KEYS[1]) == 0 then
    return {0, "PLAYER_NOT_FOUND"}
end

local score = tonumber(redis.call("HGET", KEYS[1], "score"))
local level = tonumber(redis.call("HGET", KEYS[1], "level"))

if not score or not level
    or score < 0 or level < 1
    or score ~= math.floor(score)
    or level ~= math.floor(level) then
    return redis.error_reply("INVALID_PLAYER_DATA")
end

local leaderboard_type = redis.call("TYPE", KEYS[2]).ok

if leaderboard_type ~= "none" and leaderboard_type ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end

local cost = level * 100

-- 잔액이 부족하면 아무것도 변경하지 않음
if score < cost then
    return {0, "INSUFFICIENT_GOLD", score, cost}
end

local new_score = redis.call("HINCRBY", KEYS[1], "score", -cost)
local new_level = redis.call("HINCRBY", KEYS[1], "level", 1)

redis.call("ZADD", KEYS[2], new_score, ARGV[1])

return {1, new_score, new_level, cost}