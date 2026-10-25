-- KEYS[1]: player:<player_id>
-- KEYS[2]: leaderboard
-- KEYS[3]: rate_limit:click:<player_id>
-- KEYS[4]: session:<token>
--
-- ARGV[1]: player_id
-- ARGV[2]: nickname
-- ARGV[3]: 현재 시각(ms)
-- ARGV[4]: 초기 토큰 수
-- ARGV[5]: 버킷 TTL(초)
-- ARGV[6]: 세션 TTL(초)

local player_id = ARGV[1]
local nickname = ARGV[2]
local now_ms = tonumber(ARGV[3])
local capacity = tonumber(ARGV[4])
local bucket_ttl = tonumber(ARGV[5])
local session_ttl = tonumber(ARGV[6])

-- 쓰기 전에 입력값 검사
if not player_id or player_id == "" then
    return redis.error_reply("INVALID_PLAYER_ID")
end

if not nickname or nickname == "" then
    return redis.error_reply("INVALID_NICKNAME")
end

if not now_ms or now_ms < 0 or now_ms ~= math.floor(now_ms) then
    return redis.error_reply("INVALID_TIMESTAMP")
end

if not capacity or capacity <= 0 then
    return redis.error_reply("INVALID_CAPACITY")
end

if not bucket_ttl or bucket_ttl <= 0
    or bucket_ttl ~= math.floor(bucket_ttl) then
    return redis.error_reply("INVALID_BUCKET_TTL")
end

if not session_ttl or session_ttl <= 0
    or session_ttl ~= math.floor(session_ttl) then
    return redis.error_reply("INVALID_SESSION_TTL")
end

-- 기존 플레이어·버킷·세션을 덮어쓰지 않기
if redis.call("EXISTS", KEYS[1], KEYS[3], KEYS[4]) > 0 then
    return {0, "KEY_ALREADY_EXISTS"}
end

-- leaderboard가 다른 자료형이면 쓰기 전에 중단
local leaderboard_type = redis.call("TYPE", KEYS[2]).ok

if leaderboard_type ~= "none" and leaderboard_type ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end

if redis.call("ZSCORE", KEYS[2], player_id) then
    return {0, "PLAYER_ALREADY_IN_LEADERBOARD"}
end

-- 1. 플레이어 생성
redis.call(
    "HSET", KEYS[1],
    "nickname", nickname,
    "score", 0,
    "level", 1,
    "total_clicks", 0,
    "created_at_ms", ARGV[3]
)

-- 2. 랭킹 등록
redis.call("ZADD", KEYS[2], 0, player_id)

-- 3. 토큰 버킷 생성
redis.call(
    "HSET", KEYS[3],
    "tokens", capacity,
    "last_refill_ms", ARGV[3]
)
redis.call("EXPIRE", KEYS[3], bucket_ttl)

-- 4. 세션 생성
redis.call(
    "SET", KEYS[4], player_id,
    "EX", session_ttl
)

return {1, player_id}